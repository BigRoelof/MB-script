#!/usr/bin/env python3
"""Zet Moneybird-data om naar LaTeX-tabellen voor het (half)jaarverslag van Thalia.

Gebruik:
    # persoonlijk API-token uit Moneybird in .env (zie .env.example) of in de omgeving
    python3 jaarverslag.py --start 2026-09-01 --end 2027-08-31

Zonder --start/--end wordt het lopende collegejaar (1 september t/m 31 augustus)
gebruikt. Alle tabellen komen als losse .tex-bestanden in de map --out
(standaard ./output) en zijn met \\input{} in het verslag op te nemen.

Alleen de Python-standaardbibliotheek is nodig (Python 3.11 of nieuwer).
"""

from __future__ import annotations

import argparse
import io
import calendar
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

API_BASE = "https://moneybird.com/api/v2"
CODE_RE = re.compile(r"^\s*(C\d{3})\b", re.IGNORECASE)
# Evenementlabel in de omschrijving van een boekregel: [E1234], met het event-id uit concrexit
LABEL_RE = re.compile(r"\[E(\d+)\]", re.IGNORECASE)
# Kaal label in de documentnaam, zoals de penningmeester het typt: "2627017 E1206", "F2627001 E1205".
# Alleen als los woord met hoofdletter E, dus niet midden in een concrexit-uuid ("542cdaa0-e123-...").
BARE_LABEL_RE = re.compile(r"(?<!\S)E(\d+)(?!\S)")
# Boekingsdatum en evenementdatum mogen standaard zoveel dagen uit elkaar liggen
DEFAULT_MAX_DAYS = 60


def category_digit(code: str) -> str:
    """C201 -> '2': het cijfer na de C bepaalt de categorie."""
    return code[1]
ZERO = Decimal("0")


# --------------------------------------------------------------------------
# Toegang tot Moneybird
# --------------------------------------------------------------------------

class MoneybirdClient:
    """Minimale client voor de Moneybird REST API (v2)."""

    def __init__(self, token: str, administration_id: str):
        self.token = token
        self.base = f"{API_BASE}/{administration_id}"

    def get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base}/{path}.json" + (f"?{query}" if query else "")
        for attempt in range(6):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt < 5:
                    wait = int(err.headers.get("Retry-After", "30"))
                    print(f"  Moneybird rate limit, {wait}s wachten...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                body = err.read().decode("utf-8", "replace")[:500]
                raise SystemExit(f"Moneybird gaf fout {err.code} op {path}: {body}")
        raise SystemExit(f"Te vaak rate limited op {path}")


class FixtureClient:
    """Leest opgeslagen API-antwoorden uit een map, om zonder token te testen."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, path: str, params: dict | None = None):
        params = params or {}
        if int(params.get("page", 1)) > 1:
            return []
        key = path.replace("/", "_")
        file = self.directory / f"{key}.json"
        if not file.exists():
            raise SystemExit(f"Fixture ontbreekt: {file}")
        return json.loads(file.read_text(encoding="utf-8"))


def paged(client, path: str, params: dict, key: str | None = None) -> list:
    """Haalt alle pagina's op. `key` is nodig bij rapporten (object met lijst)."""
    out, page = [], 1
    while True:
        data = client.get(path, {**params, "page": page, "per_page": 100})
        items = data.get(key, []) if (key and isinstance(data, dict)) else data
        out.extend(items)
        if len(items) < 100:
            return out
        page += 1


# --------------------------------------------------------------------------
# Rekenwerk
# --------------------------------------------------------------------------

@dataclass
class Amounts:
    debit: Decimal = ZERO     # kosten
    credit: Decimal = ZERO    # opbrengsten

    @property
    def result(self) -> Decimal:
        return self.credit - self.debit

    def __iadd__(self, other: "Amounts"):
        self.debit += other.debit
        self.credit += other.credit
        return self

    def is_zero(self) -> bool:
        return self.debit == 0 and self.credit == 0


@dataclass
class Row:
    code: str
    label: str
    budget: Amounts = field(default_factory=Amounts)
    actual: Amounts = field(default_factory=Amounts)

    @property
    def difference(self) -> Decimal:
        return self.actual.result - self.budget.result


def dec(value) -> Decimal:
    return Decimal(str(value or "0"))


# --------------------------------------------------------------------------
# Begroting
# --------------------------------------------------------------------------

BUDGET_DIR = Path(__file__).with_name("begroting")
BUDGET_FILE_RE = re.compile(r"^(\d{4})-(\d{4})\.csv$")
BUDGET_META_RE = re.compile(r"^#\s*(status|vastgesteld)\s*:\s*(.*?)\s*$", re.IGNORECASE)


@dataclass
class Budget:
    """De begroting van één collegejaar: per code kosten (debit), opbrengsten (credit) en toelichting."""
    year: int | None                    # 2026 = collegejaar 2026-2027; None bij een los bestand
    amounts: dict[str, Amounts] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    status: str = "concept"             # "concept" of "vastgesteld" (door de ALV; dan vergrendeld)
    adopted: dt.date | None = None      # datum van vaststelling
    path: Path | None = None

    @property
    def locked(self) -> bool:
        return self.status == "vastgesteld"

    @property
    def label(self) -> str:
        return year_label(self.year) if self.year is not None else (self.path.stem if self.path else "")


def college_year(day: dt.date) -> int:
    """Collegejaar waarin een datum valt (1 september t/m 31 augustus), als beginjaar."""
    return day.year if day.month >= 9 else day.year - 1


def year_label(year: int) -> str:
    return f"{year}-{year + 1}"


def budget_path(year: int, directory: Path = BUDGET_DIR) -> Path:
    return directory / f"{year_label(year)}.csv"


def budget_years(directory: Path = BUDGET_DIR) -> list[int]:
    """Collegejaren waarvoor een begrotingsbestand bestaat."""
    if not directory.is_dir():
        return []
    return sorted(int(m.group(1)) for f in directory.iterdir()
                  if (m := BUDGET_FILE_RE.match(f.name)) and int(m.group(2)) == int(m.group(1)) + 1)


def load_budget(path: Path, year: int | None = None) -> Budget:
    """Leest een begroting: CSV met kolommen code,debit,credit en optioneel note.

    Bovenin mogen regels "# status: vastgesteld" en "# vastgesteld: 2026-10-20" staan.
    """
    budget = Budget(year, path=path)
    text = path.read_text(encoding="utf-8-sig")
    lines = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            if m := BUDGET_META_RE.match(ln.strip()):
                key, value = m.group(1).lower(), m.group(2)
                if key == "status":
                    budget.status = "vastgesteld" if value.lower() == "vastgesteld" else "concept"
                elif value:
                    try:
                        budget.adopted = dt.date.fromisoformat(value)
                    except ValueError:
                        raise SystemExit(f"{path.name}: '{value}' is geen geldige datum (verwacht 2026-10-20).")
        elif ln.strip():
            lines.append(ln)
    delimiter = ";" if lines and lines[0].count(";") > lines[0].count(",") else ","
    for rec in csv.DictReader(lines, delimiter=delimiter):
        code = (rec.get("code") or "").strip().upper()
        if not CODE_RE.fullmatch(code):
            raise SystemExit(f"{path.name}: '{code}' is geen geldige code (verwacht bijvoorbeeld C101).")
        if code in budget.amounts:
            raise SystemExit(f"{path.name}: code {code} staat er twee keer in.")
        try:
            amounts = Amounts(*(dec((rec.get(k) or "0").strip().replace(",", ".")) for k in ("debit", "credit")))
        except ArithmeticError:
            raise SystemExit(f"{path.name}: code {code} heeft een ongeldig bedrag.")
        budget.amounts[code] = amounts
        if note := (rec.get("note") or "").strip():
            budget.notes[code] = note
    return budget


def find_budget(start: dt.date, directory: Path = BUDGET_DIR) -> Budget | None:
    """De begroting van het collegejaar waarin de periode begint, als die er is."""
    year = college_year(start)
    path = budget_path(year, directory)
    return load_budget(path, year) if path.exists() else None


def save_budget(budget: Budget, path: Path) -> None:
    """Schrijft de begroting (alleen regels met een bedrag of toelichting), via een tijdelijk bestand."""
    header = [f"# Begroting Thalia {budget.label}. Bewerken via het dashboard (Begroting) of hier met de hand.",
              "# Bedragen excl. btw: debit = kosten, credit = opbrengsten.",
              f"# status: {budget.status}"]
    if budget.adopted:
        header.append(f"# vastgesteld: {budget.adopted.isoformat()}")
    out = io.StringIO()
    out.write("\n".join(header) + "\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["code", "debit", "credit", "note"])
    for code in sorted(set(budget.amounts) | set(budget.notes)):
        a = budget.amounts.get(code, Amounts())
        note = budget.notes.get(code, "")
        if a.is_zero() and not note:
            continue
        writer.writerow([code, f"{a.debit:.2f}", f"{a.credit:.2f}", note])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(out.getvalue(), encoding="utf-8")
    os.replace(tmp, path)


def load_codes(path: Path, category_digits: list[str]) -> dict[str, str]:
    """Leest codes.csv (kolommen code,name) en controleert het bestand."""
    if not path.exists():
        raise SystemExit(f"Codebestand niet gevonden: {path}")
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    delimiter = ";" if lines and lines[0].count(";") > lines[0].count(",") else ","
    codes: dict[str, str] = {}
    for n, rec in enumerate(csv.DictReader(lines, delimiter=delimiter), start=1):
        code = (rec.get("code") or "").strip().upper()
        name = (rec.get("name") or "").strip()
        if not CODE_RE.fullmatch(code):
            raise SystemExit(f"{path.name}: '{code}' is geen geldige code (verwacht bijvoorbeeld C101).")
        if not name:
            raise SystemExit(f"{path.name}: code {code} heeft geen naam.")
        if code in codes:
            raise SystemExit(f"{path.name}: code {code} staat er twee keer in.")
        if category_digit(code) not in category_digits:
            raise SystemExit(f"{path.name}: code {code} hoort bij geen categorie in config.toml "
                             f"(bekend: {', '.join(category_digits)}).")
        codes[code] = name
    if not codes:
        raise SystemExit(f"{path.name} bevat geen codes (verwacht kolommen code,name).")
    return codes


def _code_line(code: str, name: str, delimiter: str) -> str:
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter, lineterminator="").writerow([code, name])
    return out.getvalue()


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Geef de regel een naam.")
    if len(name) > 80:
        raise ValueError("De naam is te lang (maximaal 80 tekens).")
    return name


def edit_codes(path: Path, category_digits: list[str], code: str, name: str, *, new: bool) -> None:
    """Voegt een code toe aan codes.csv of hernoemt er een. Commentaar en volgorde blijven staan.

    Geeft ValueError met een leesbare melding als het niet kan.
    """
    code, name = code.strip().upper(), _clean_name(name)
    if not CODE_RE.fullmatch(code):
        raise ValueError(f"'{code}' is geen geldige code (verwacht bijvoorbeeld C108).")
    if category_digit(code) not in category_digits:
        raise ValueError(f"Code {code} hoort bij geen categorie.")
    original = path.read_text(encoding="utf-8-sig")
    existing = load_codes(path, category_digits)
    if new and code in existing:
        raise ValueError(f"Code {code} bestaat al ({existing[code]}).")
    if not new and code not in existing:
        raise ValueError(f"Code {code} bestaat niet.")

    lines = original.splitlines()
    data = [i for i, ln in enumerate(lines) if ln.strip() and not ln.lstrip().startswith("#")]
    delimiter = ";" if lines[data[0]].count(";") > lines[data[0]].count(",") else ","

    def line_code(i):
        return lines[i].split(delimiter, 1)[0].strip().strip('"').upper()

    if new:
        # Na de laatste kleinere code van dezelfde categorie, anders vóór de eerste grotere, anders achteraan
        same = [i for i in data[1:] if category_digit(line_code(i)) == category_digit(code)]
        before = [i for i in same if line_code(i) < code]
        after = [i for i in same if line_code(i) > code]
        at = before[-1] + 1 if before else (after[0] if after else len(lines))
        lines.insert(at, _code_line(code, name, delimiter))
    else:
        i = next(i for i in data[1:] if line_code(i) == code)
        lines[i] = _code_line(code, name, delimiter)

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        load_codes(tmp, category_digits)       # nog steeds geldig?
    except SystemExit as err:
        tmp.unlink()
        raise ValueError(str(err.code))
    os.replace(tmp, path)


@dataclass
class Event:
    name: str
    date: dt.date
    organizer: str


def parse_date(text: str) -> dt.date:
    """Accepteert 2026-10-11 (ook met tijd erachter) en 11-10-2026."""
    text = text.strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return dt.datetime.strptime(text, "%d-%m-%Y").date()


def load_events(path: Path) -> dict[str, Event]:
    """Leest de evenementenexport uit concrexit (kolommen id,name,organizer,date)."""
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    delimiter = ";" if lines and lines[0].count(";") > lines[0].count(",") else ","
    events: dict[str, Event] = {}
    for rec in csv.DictReader(lines, delimiter=delimiter):
        event_id = (rec.get("id") or "").strip().upper().removeprefix("E")
        name = (rec.get("name") or "").strip()
        if not event_id.isdigit():
            raise SystemExit(f"{path.name}: '{rec.get('id')}' is geen geldig event-id (verwacht een getal).")
        if event_id in events:
            raise SystemExit(f"{path.name}: event {event_id} staat er twee keer in.")
        try:
            date = parse_date(rec.get("date") or "")
        except ValueError:
            raise SystemExit(f"{path.name}: event {event_id} heeft geen geldige datum ('{rec.get('date')}').")
        organizer = (rec.get("organizer") or "").strip() or "Other"
        events[event_id] = Event(name or f"Event {event_id}", date, organizer)
    return events


def build_results(cfg, projects, revenue, expenses, budget, warnings):
    """Groepeert projecten op code en levert {categorie-cijfer: [Row]} op."""
    row_labels: dict[str, str] = cfg["rows"]
    per_code: dict[str, Amounts] = {}
    example_name: dict[str, str] = {}

    names = {p["id"]: p["name"] for p in projects}
    rev = {r["project_id"]: dec(r["amount"]) for r in revenue}
    exp = {r["project_id"]: dec(r["amount"]) for r in expenses}

    for pid in sorted(set(rev) | set(exp), key=str):
        amounts = Amounts(debit=exp.get(pid, ZERO), credit=rev.get(pid, ZERO))
        if amounts.is_zero():
            continue
        if pid is None:
            warnings.append(
                f"Geboekt zonder project: kosten {fmt_plain(amounts.debit)}, "
                f"opbrengsten {fmt_plain(amounts.credit)}. Deze bedragen staan niet in de tabellen.")
            continue
        name = names.get(pid, f"onbekend project {pid}")
        match = CODE_RE.match(name)
        if not match:
            warnings.append(f"Project zonder code: '{name}' "
                            f"(kosten {fmt_plain(amounts.debit)}, opbrengsten {fmt_plain(amounts.credit)}). "
                            "Niet opgenomen in de tabellen.")
            continue
        code = match.group(1).upper()
        per_code.setdefault(code, Amounts())
        per_code[code] += amounts
        example_name.setdefault(code, name)

    digits = [c["digit"] for c in cfg["categories"]]
    for code in per_code:
        if category_digit(code) not in digits:
            warnings.append(f"Code {code} ('{example_name[code]}') hoort bij geen categorie in config.toml. "
                            "Niet opgenomen in de tabellen.")
        elif code not in row_labels:
            warnings.append(f"Code {code} ('{example_name[code]}') staat niet in codes.csv; "
                            "regel krijgt een tijdelijke naam.")
    for code in sorted(budget):
        if category_digit(code) not in digits:
            warnings.append(f"Begroting: code {code} hoort bij geen categorie. Niet opgenomen in de tabellen.")
        elif code not in row_labels:
            warnings.append(f"Begroting: code {code} staat niet in codes.csv; regel krijgt een tijdelijke naam.")

    hide_empty = cfg["layout"].get("hide_empty_rows", True)
    show_codes = cfg["layout"].get("show_codes", False)
    tables: dict[str, list[Row]] = {}
    for digit in digits:
        codes = sorted({c for c in row_labels if category_digit(c) == digit}
                       | {c for c in per_code if category_digit(c) == digit}
                       | {c for c in budget if category_digit(c) == digit})
        rows = []
        for code in codes:
            label = row_labels.get(code, f"Code {code} (niet in codes.csv)")
            if show_codes:
                label = f"{code} {label}"
            row = Row(code, label, budget.get(code, Amounts()), per_code.get(code, Amounts()))
            if hide_empty and row.actual.is_zero() and row.budget.is_zero():
                continue
            rows.append(row)
        tables[digit] = rows
    return tables


def total(rows: list[Row], label="TOTAL") -> Row:
    t = Row("", label)
    for r in rows:
        t.budget += r.budget
        t.actual += r.actual
    return t


# --------------------------------------------------------------------------
# LaTeX-uitvoer
# --------------------------------------------------------------------------

LATEX_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                 "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}",
                 "\\": r"\textbackslash{}"}

NUMBER_FMT = {"thousands": ",", "decimal": "."}


def tex(text: str) -> str:
    return "".join(LATEX_SPECIAL.get(ch, ch) for ch in str(text))


def fmt_plain(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}"
    s = s.replace(",", "\0").replace(".", NUMBER_FMT["decimal"]).replace("\0", NUMBER_FMT["thousands"])
    return ("-" if q < 0 else "") + s


def fmt(value: Decimal) -> str:
    s = fmt_plain(value)
    return "$-$" + s[1:] if s.startswith("-") else s


def results_table(rows: list[Row], total_label="TOTAL", with_budget=False) -> str:
    """Resultatentabel. Met begroting erbij: account, budget, debit, credit, result, difference
    (budget = begroot resultaat, difference = result - budget)."""
    if with_budget:
        spec, ncols = "rrrrr", 6
        group = (" & & \\multicolumn{3}{c}{\\textsc{result} (\\texteuro)} & \\\\\n"
                 "\\cmidrule(lr){3-5}\n"
                 "\\textsc{account} & \\textsc{budget} & \\textsc{debit} & \\textsc{credit} & "
                 "\\textsc{result} & \\textsc{difference} \\\\\n")
    else:
        spec, ncols = "rrr", 4
        group = (" & \\multicolumn{3}{c}{\\textsc{result} (\\texteuro)} \\\\\n"
                 "\\cmidrule(lr){2-4}\n"
                 "\\textsc{account} & \\textsc{debit} & \\textsc{credit} & \\textsc{result} \\\\\n")
    head = (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        f"\\begin{{tabularx}}{{\\textwidth}}{{@{{}}>{{\\raggedright\\arraybackslash}}X {spec}@{{}}}}\n"
        "\\toprule\n" + group + "\\midrule\n"
    )

    def line(r: Row, bold=False):
        cells = [tex(r.label), fmt(r.actual.debit), fmt(r.actual.credit), fmt(r.actual.result)]
        if with_budget:
            cells = [cells[0], fmt(r.budget.result), *cells[1:], fmt(r.difference)]
        if bold:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        return " & ".join(cells) + " \\\\\n"

    body = "".join(line(r) for r in rows)
    if not rows:
        body = f"\\multicolumn{{{ncols}}}{{l}}{{\\emph{{No bookings in this period}}}} \\\\\n"
    return (head + body + "\\midrule\n" + line(total(rows, total_label), bold=True)
            + "\\bottomrule\n\\end{tabularx}\n")


def budget_table(rows: list[Row], notes: dict[str, str] | None = None, total_label="TOTAL",
                 skip_empty=True) -> str:
    """De begroting zelf (voor de ALV): account, debit, credit, result, met toelichting als die er is."""
    if skip_empty:
        rows = [r for r in rows if not r.budget.is_zero()]
    notes = notes or {}
    with_notes = any(notes.get(r.code) for r in rows)
    spec = "rrr" + (" >{\\raggedright\\arraybackslash}p{0.3\\textwidth}" if with_notes else "")
    ncols = 5 if with_notes else 4
    head = (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        f"\\begin{{tabularx}}{{\\textwidth}}{{@{{}}>{{\\raggedright\\arraybackslash}}X {spec}@{{}}}}\n"
        "\\toprule\n"
        " & \\multicolumn{3}{c}{\\textsc{budget} (\\texteuro)}" + (" &" if with_notes else "") + " \\\\\n"
        "\\cmidrule(lr){2-4}\n"
        "\\textsc{account} & \\textsc{debit} & \\textsc{credit} & \\textsc{result}"
        + (" & \\textsc{explanation}" if with_notes else "") + " \\\\\n"
        "\\midrule\n"
    )

    def line(r: Row, bold=False):
        cells = [tex(r.label), fmt(r.budget.debit), fmt(r.budget.credit), fmt(r.budget.result)]
        if bold:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        if with_notes:
            cells.append(tex(notes.get(r.code, "")))
        return " & ".join(cells) + " \\\\\n"

    body = "".join(line(r) for r in rows)
    if not rows:
        body = f"\\multicolumn{{{ncols}}}{{l}}{{\\emph{{Nothing budgeted}}}} \\\\\n"
    return (head + body + "\\midrule\n" + line(total(rows, total_label), bold=True)
            + "\\bottomrule\n\\end{tabularx}\n")


# --------------------------------------------------------------------------
# Tabellen per evenement
# --------------------------------------------------------------------------

COST_LEDGER_TYPES = {"expenses", "direct_costs", "other_income_expenses"}


@dataclass
class EventLine:
    code: str
    event_id: str         # "" = regel zonder evenementlabel
    date: dt.date | None  # documentdatum van de boeking
    description: str
    reference: str
    amounts: Amounts
    has_period: bool      # de rapporten spreiden zulke regels over hun periode


def reference_labels(reference: str) -> set[str]:
    """Event-id's in een documentnaam: [E1202] (concrexit) en los E1206 (handmatig)."""
    return {str(int(x)) for x in LABEL_RE.findall(reference) + BARE_LABEL_RE.findall(reference)}


def event_lines(documents: dict, projects, ledger, label_codes: set[str]) -> list[EventLine]:
    """Haalt de boekregels met een evenementlabel uit de documenten, plus de regels
    zonder label onder de codes waar een label verwacht wordt (label_codes).

    Concepten en nog niet opgeslagen documenten tellen niet mee, net als in de
    Moneybird-rapporten. Bedragen zijn excl. btw.
    """
    code_of = {p["id"]: m.group(1).upper() for p in projects if (m := CODE_RE.match(p["name"]))}
    ledger_type = {la["id"]: la.get("account_type") for la in ledger}
    lines: list[EventLine] = []

    def add(doc, project_id, description, debit, credit, period=None):
        code = code_of.get(project_id, "")
        m = LABEL_RE.search(description or "")
        event_id = str(int(m.group(1))) if m else ""
        if not event_id:
            # Anders het label in de documentnaam: concrexit zet het daar tussen haken
            # ("Food Maandborrel September [9107] [E1202]"), de penningmeester los ("2627017 E1206").
            # Alleen gebruiken als die naam precies één evenement noemt.
            in_reference = reference_labels(doc.get("reference") or "")
            if len(in_reference) == 1:
                event_id = in_reference.pop()
        if not event_id and code not in label_codes:
            return
        raw_date = doc.get("date") or doc.get("invoice_date")  # verkoopfacturen hebben invoice_date
        date = dt.date.fromisoformat(raw_date) if raw_date else None
        lines.append(EventLine(code, event_id, date, description or "",
                               doc.get("reference") or "", Amounts(debit, credit), bool(period)))

    for doc in documents["sales_invoices"]:
        if doc.get("state") in ("draft", "scheduled"):
            continue
        for d in doc.get("details", []):
            add(doc, d.get("project_id"), d.get("description"),
                ZERO, dec(d.get("total_price_excl_tax_with_discount")), d.get("period"))
    for doc in documents["external_sales_invoices"]:
        if doc.get("state") == "new":
            continue
        for d in doc.get("details", []):
            add(doc, d.get("project_id"), d.get("description"),
                ZERO, dec(d.get("total_price_excl_tax_with_discount")), d.get("period"))
    for kind in ("purchase_invoices", "receipts"):
        for doc in documents[kind]:
            if doc.get("state") == "new":
                continue
            revenue = doc.get("revenue_invoice", False)
            for d in doc.get("details", []):
                amount = dec(d.get("total_price_excl_tax_with_discount"))
                add(doc, d.get("project_id"), d.get("description"),
                    ZERO if revenue else amount, amount if revenue else ZERO, d.get("period"))
    for doc in documents["general_journal_documents"]:
        for e in doc.get("general_journal_document_entries", []):
            kind = ledger_type.get(e.get("ledger_account_id"))
            net = dec(e.get("debit")) - dec(e.get("credit"))
            if kind == "revenue":
                add(doc, e.get("project_id"), e.get("description"), ZERO, -net)
            elif kind in COST_LEDGER_TYPES:
                add(doc, e.get("project_id"), e.get("description"), net, ZERO)
    return lines


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower()) or "other"


@dataclass
class EventRow:
    event_id: str
    name: str
    date: dt.date | None  # None = evenement staat niet in events.csv
    amounts: Amounts


def event_table(rows: list[EventRow]) -> str:
    grand = Amounts()
    body = ""
    for r in rows:
        a = r.amounts
        grand += a
        date = f"{r.date:%d-%m-%Y}" if r.date else ""
        body += f"{tex(r.name)} & {date} & {fmt(a.debit)} & {fmt(a.credit)} & {fmt(a.result)} \\\\\n"
    if not rows:
        body = "\\multicolumn{5}{l}{\\emph{No events in this period}} \\\\\n"
    return (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        "\\begin{tabularx}{\\textwidth}{@{}>{\\raggedright\\arraybackslash}X l rrr@{}}\n"
        "\\toprule\n"
        "\\textsc{event} & \\textsc{date} & \\textsc{debit} & \\textsc{credit} & \\textsc{result} \\\\\n"
        "\\midrule\n" + body + "\\midrule\n"
        f"\\textbf{{TOTAL}} & & \\textbf{{{fmt(grand.debit)}}} & \\textbf{{{fmt(grand.credit)}}} & "
        f"\\textbf{{{fmt(grand.result)}}} \\\\\n"
        "\\bottomrule\n\\end{tabularx}\n"
    )


def organizer_groups(lines: list[EventLine], events: dict[str, Event], start: dt.date, end: dt.date,
                     organizer_names: dict[str, str]) -> list[tuple[str, list[EventRow]]]:
    """Per organizer zijn evenementen: (kop, [EventRow]).

    Een evenement komt erin als het in de periode valt of als er in de periode op geboekt is.
    Volgorde van de organizers: zoals ze voor het eerst in de evenementenlijst staan.
    Labels die niet in de lijst staan komen in een aparte tabel onderaan.
    """
    per_event: dict[str, Amounts] = {}
    for l in lines:
        if l.event_id:
            per_event.setdefault(l.event_id, Amounts())
            per_event[l.event_id] += l.amounts

    by_organizer: dict[str, list[str]] = {}
    for event_id, e in events.items():
        if event_id in per_event or start <= e.date <= end:
            by_organizer.setdefault(e.organizer, []).append(event_id)

    result = []
    for organizer, ids in by_organizer.items():
        ids.sort(key=lambda i: (events[i].date, int(i)))
        rows = [EventRow(i, events[i].name, events[i].date, per_event.get(i, Amounts())) for i in ids]
        result.append((organizer_names.get(organizer, organizer), rows))

    unknown = sorted((i for i in per_event if i not in events), key=int)
    if unknown:
        heading = "Unknown events" if events else "Events"
        result.append((heading, [EventRow(i, f"E{i}", None, per_event[i]) for i in unknown]))
    return result


def event_warnings(lines: list[EventLine], events: dict[str, Event], max_days: int) -> list[str]:
    warnings = []
    unlabeled: dict[str, list[EventLine]] = {}
    for l in lines:
        if not l.event_id:
            unlabeled.setdefault(l.code, []).append(l)
    for code, ls in sorted(unlabeled.items()):
        total = Amounts()
        for l in ls:
            total += l.amounts
        warnings.append(
            f"Evenementen {code}: {len(ls)} regel(s) zonder [E…]-label (kosten {fmt_plain(total.debit)}, "
            f"opbrengsten {fmt_plain(total.credit)}), bijvoorbeeld '{ls[0].description}' ({ls[0].reference}).")
    if events:
        for l in lines:
            if not l.event_id:
                continue
            event = events.get(l.event_id)
            if event is None:
                warnings.append(f"Evenementen: E{l.event_id} staat niet in de evenementenlijst "
                                f"('{l.description}', {l.reference}). Typfout in het nummer?")
            elif l.date and abs((l.date - event.date).days) > max_days:
                warnings.append(f"Evenementen: '{l.description}' ({l.reference}, {l.date:%d-%m-%Y}) "
                                f"ligt {abs((l.date - event.date).days)} dagen van E{l.event_id} "
                                f"'{event.name}' ({event.date:%d-%m-%Y}). Juist evenement?")
    periodic = [l for l in lines if l.has_period]
    if periodic:
        warnings.append(f"Evenementen: {len(periodic)} regel(s) met een eigen periode; de rapporten spreiden die "
                        "over hun periode, dus de evenementtabellen kunnen afwijken.")
    return warnings


def event_checks(lines: list[EventLine], label_codes: set[str], report_by_code: dict[str, Amounts]):
    """De boekregels onder de labelcodes (met en zonder label) moeten optellen tot de projectrapporten."""
    got, want = Amounts(), Amounts()
    for l in lines:
        if l.code in label_codes:
            got += l.amounts
    for code in label_codes:
        want += report_by_code.get(code, Amounts())
    checks = []
    for what, a, b in (("kosten", got.debit, want.debit), ("opbrengsten", got.credit, want.credit)):
        name = f"Evenementen: boekregels labelcodes = projecten ({what})"
        diff = a - b
        if abs(diff) <= TOLERANCE:
            checks.append((name, "OK", fmt_plain(a)))
        else:
            checks.append((name, "AFWIJKING", f"boekregels {fmt_plain(a)} vs projecten {fmt_plain(b)} "
                                              f"(verschil {fmt_plain(diff)})"))
    return checks


TOLERANCE = Decimal("0.01")


def run_checks(tables, overview_rows, revenue, expenses, pl):
    """Rekent de cijfers na. Geeft (naam, status, detail) terug; status is OK, AFWIJKING of LET OP."""
    checks = []

    def check(name, expected, actual, label_expected, label_actual, soft=False):
        diff = actual - expected
        if abs(diff) <= TOLERANCE:
            checks.append((name, "OK", f"{fmt_plain(actual)}"))
        else:
            checks.append((name, "LET OP" if soft else "AFWIJKING",
                           f"{label_actual} {fmt_plain(actual)} vs {label_expected} {fmt_plain(expected)} "
                           f"(verschil {fmt_plain(diff)})"))

    pl_rev, pl_exp = dec(pl.get("total_revenue")), dec(pl.get("total_expenses"))
    pl_net = dec(pl["net_profit"]) if "net_profit" in pl else pl_rev - pl_exp
    grand = total(overview_rows)

    # 1. Projectrapporten tegenover de winst-en-verliesrekening (inclusief boekingen zonder project)
    sum_rev = sum((dec(r["amount"]) for r in revenue), ZERO)
    sum_exp = sum((dec(r["amount"]) for r in expenses), ZERO)
    check("Projecten: opbrengsten = W&V", pl_rev, sum_rev, "W&V", "projecten")
    check("Projecten: kosten = W&V", pl_exp, sum_exp, "W&V", "projecten")

    # 2. Overzichtstabel tegenover de winst-en-verliesrekening
    check("Overzicht: opbrengsten = W&V", pl_rev, grand.actual.credit, "W&V", "overzicht")
    check("Overzicht: kosten = W&V", pl_exp, grand.actual.debit, "W&V", "overzicht")
    check("Overzicht: resultaat = W&V", pl_net, grand.actual.result, "W&V", "overzicht")

    # 3. Alle regels in alle categorietabellen opgeteld tegenover het overzicht
    every_row = total([r for rows in tables.values() for r in rows])
    check("Categorieen: opbrengsten = overzicht", grand.actual.credit, every_row.actual.credit, "overzicht", "regels")
    check("Categorieen: kosten = overzicht", grand.actual.debit, every_row.actual.debit, "overzicht", "regels")
    check("Categorieen: begroting = overzicht", grand.budget.result, every_row.budget.result, "overzicht", "regels")

    return checks


EXAMPLE_DOC = r"""\documentclass[a4paper,11pt]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{textcomp}
\begin{document}
\section*{Financial report %(period)s}
\subsection*{Results overview}
{\small\input{results_overview}}
%(sections)s
%(event_sections)s
%(budget_sections)s
\end{document}
"""


# --------------------------------------------------------------------------
# Hoofdprogramma
# --------------------------------------------------------------------------

def parse_args(argv=None):
    today = dt.date.today()
    year = today.year if today.month >= 9 else today.year - 1
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(year, 9, 1),
                   help="begindatum, eerste dag van een maand (standaard 1 september)")
    p.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(year + 1, 8, 31),
                   help="einddatum, laatste dag van een maand (standaard 31 augustus)")
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    p.add_argument("--codes", type=Path, default=Path(__file__).with_name("codes.csv"),
                   help="CSV met kolommen code,name (standaard codes.csv naast het script)")
    p.add_argument("--events", type=Path,
                   help="evenementenexport uit concrexit, kolommen id,name,organizer,date "
                        "(standaard events.csv naast het script, of in de --fixtures-map)")
    p.add_argument("--budget", type=Path,
                   help="begroting, CSV met kolommen code,debit,credit(,note) (standaard "
                        "begroting/<collegejaar>.csv naast het script, bijv. begroting/2026-2027.csv)")
    p.add_argument("--no-budget", action="store_true", help="geen begroting gebruiken")
    p.add_argument("--out", type=Path, default=Path("output"))
    p.add_argument("--fixtures", type=Path, help="map met opgeslagen API-antwoorden (testen zonder token)")
    return p.parse_args(argv)


def validate_period(start: dt.date, end: dt.date):
    last_day = calendar.monthrange(end.year, end.month)[1]
    if start.day != 1 or end.day != last_day:
        raise SystemExit("Moneybird-rapporten werken met hele maanden: --start moet de 1e van een maand "
                         "zijn en --end de laatste dag van een maand.")
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if months < 1 or months > 12:
        raise SystemExit("De periode moet tussen 1 en 12 maanden lang zijn.")


def load_config(config_path: Path, codes_path: Path) -> tuple[dict, set[str]]:
    """Leest config.toml en codes.csv en controleert ze. Geeft (cfg, label_codes)."""
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    cfg["rows"] = load_codes(codes_path, [c["digit"] for c in cfg["categories"]])
    events_cfg = cfg.setdefault("events", {})
    if "groups" in events_cfg:
        raise SystemExit("config.toml: [[events.groups]] bestaat niet meer; gebruik [events] label_codes = [...] "
                         "(de groepering komt nu uit de organizer-kolom van events.csv).")
    label_codes = {c.upper() for c in events_cfg.get("label_codes", [])}
    unknown = sorted(c for c in label_codes if c not in cfg["rows"])
    if unknown:
        raise SystemExit(f"config.toml [events] label_codes: onbekende code(s) {', '.join(unknown)} "
                         "(staan niet in codes.csv).")
    NUMBER_FMT["thousands"] = cfg["layout"].get("thousands_separator", ",")
    NUMBER_FMT["decimal"] = cfg["layout"].get("decimal_separator", ".")
    return cfg, label_codes


def load_dotenv(path: Path = Path(__file__).with_name(".env")) -> None:
    """Zet regels als MONEYBIRD_TOKEN=... uit .env in de omgeving.

    Variabelen die al in de omgeving staan gaan voor. Het bestand staat in .gitignore.
    """
    if not path.exists():
        return
    if os.name == "posix" and path.stat().st_mode & 0o077:
        print(f"Let op: {path} is leesbaar voor andere gebruikers; maak het privé met: chmod 600 {path}",
              file=sys.stderr)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def make_client(cfg: dict, fixtures: Path | None):
    if fixtures:
        return FixtureClient(fixtures)
    token = os.environ.get("MONEYBIRD_TOKEN")
    if not token:
        raise SystemExit("Geen Moneybird-token gevonden. Zet MONEYBIRD_TOKEN=... in het bestand .env "
                         "naast het script, of als omgevingsvariabele.")
    return MoneybirdClient(token, cfg["administration_id"])


def fetch_data(client, start: dt.date, end: dt.date) -> dict:
    """Haalt alles op wat het verslag nodig heeft (ruwe API-antwoorden)."""
    period = f"{start:%Y%m%d}..{end:%Y%m%d}"
    return {
        "projects": paged(client, "projects", {"filter": "state:all"}),
        "revenue": paged(client, "reports/revenue_by_project", {"period": period}, "revenue_by_project"),
        "expenses": paged(client, "reports/expenses_by_project", {"period": period}, "expenses_by_project"),
        "pl": client.get("reports/profit_loss", {"period": period}),
        "ledger": client.get("ledger_accounts"),
        # Voor de evenementtabellen: de boekregels zelf (de rapporten kennen geen omschrijvingen)
        "documents": {
            "sales_invoices": paged(client, "sales_invoices", {"filter": f"period:{period},state:all"}),
            "external_sales_invoices": paged(client, "external_sales_invoices", {"filter": f"period:{period}"}),
            "purchase_invoices": paged(client, "documents/purchase_invoices", {"filter": f"period:{period}"}),
            "receipts": paged(client, "documents/receipts", {"filter": f"period:{period}"}),
            "general_journal_documents": paged(client, "documents/general_journal_documents",
                                               {"filter": f"period:{period}"}),
        },
    }


def find_events(events_arg: Path | None, fixtures: Path | None) -> Path:
    """events.csv: --events, anders naast het script, anders in de fixturesmap."""
    if events_arg is not None:
        return events_arg
    candidates = [Path(__file__).with_name("events.csv")]
    if fixtures:
        candidates.append(fixtures / "events.csv")
    return next((c for c in candidates if c.exists()), candidates[0])


@dataclass
class Report:
    start: dt.date
    end: dt.date
    categories: list[dict]              # [[categories]] uit config.toml
    tables: dict[str, list[Row]]        # categorie-cijfer -> regels
    overview_rows: list[Row]            # één regel per categorie
    lines: list[EventLine]
    events: dict[str, Event]
    events_path: Path | None            # None = events.csv niet gevonden
    organizers: list[tuple[str, list[EventRow]]]
    budget: Budget | None
    checks: list[tuple[str, str, str]]
    warnings: list[str]
    files: dict[str, str]               # bestandsnaam -> LaTeX

    @property
    def grand(self) -> Row:
        return total(self.overview_rows)


def build_report(cfg: dict, label_codes: set[str], data: dict, start: dt.date, end: dt.date,
                 events_path: Path, budget: Budget | None = None) -> Report:
    """Rekent alles uit en maakt de LaTeX-bestanden (in het geheugen)."""
    warnings: list[str] = []
    if budget is not None:
        if budget.year is not None and college_year(end) != budget.year:
            warnings.append(f"De periode loopt over twee collegejaren; vergeleken met de begroting van "
                            f"{budget.label}.")
        if not budget.locked:
            warnings.append(f"De begroting {budget.label} is nog een concept (niet vastgesteld).")
    tables = build_results(cfg, data["projects"], data["revenue"], data["expenses"],
                           budget.amounts if budget else {}, warnings)
    files: dict[str, str] = {}
    with_budget = budget is not None

    overview_rows = []
    sections = []
    budget_sections = []
    budget_files = {}
    for c in cfg["categories"]:
        rows = tables[c["digit"]]
        files[f"results_{c['file']}.tex"] = results_table(rows, with_budget=with_budget)
        overview_rows.append(total(rows, c["name"]))
        sections.append(f"\\subsubsection*{{{tex(c['name'])}}}\n{{\\small\\input{{results_{c['file']}}}}}")
        if budget is not None:
            budget_files[f"budget_{c['file']}.tex"] = budget_table(rows, budget.notes)
            budget_sections.append(f"\\subsubsection*{{{tex(c['name'])}}}\n{{\\small\\input{{budget_{c['file']}}}}}")
    files = {"results_overview.tex": results_table(overview_rows, with_budget=with_budget), **files}
    if budget is not None:
        overview_budget = [Row(r.code, r.label, budget=r.budget) for r in overview_rows]
        files["budget_overview.tex"] = budget_table(overview_budget, skip_empty=False)
        files.update(budget_files)

    checks = run_checks(tables, overview_rows, data["revenue"], data["expenses"], data["pl"])

    report_by_code = {r.code: r.actual for rows in tables.values() for r in rows}
    lines = event_lines(data["documents"], data["projects"], data["ledger"], label_codes)
    events: dict[str, Event] = {}
    if events_path.exists():
        events = load_events(events_path)
    else:
        warnings.append(f"Evenementenlijst {events_path} niet gevonden: de evenementtabellen tonen alleen "
                        "nummers en de labels worden niet gecontroleerd.")
    events_cfg = cfg.get("events", {})
    organizers = organizer_groups(lines, events, start, end, events_cfg.get("organizers", {}))
    event_sections = []
    for heading, rows in organizers:
        name = f"events_{slug(heading)}"
        files[f"{name}.tex"] = event_table(rows)
        event_sections.append(f"\\subsubsection*{{{tex(heading)}}}\n{{\\small\\input{{{name}}}}}")
    checks += event_checks(lines, label_codes, report_by_code)
    warnings += event_warnings(lines, events, events_cfg.get("max_days", DEFAULT_MAX_DAYS))

    files["example.tex"] = EXAMPLE_DOC % {
        "period": f"{start:%d-%m-%Y} -- {end:%d-%m-%Y}",
        "sections": "\n".join(sections),
        "event_sections": ("\\subsection*{Events per organizer}\n" + "\n".join(event_sections))
                          if event_sections else "",
        "budget_sections": (f"\\subsection*{{Budget {budget.label}}}\n{{\\small\\input{{budget_overview}}}}\n"
                            + "\n".join(budget_sections)) if budget is not None else ""}

    return Report(start, end, cfg["categories"], tables, overview_rows,
                  lines, events, events_path if events_path.exists() else None, organizers, budget,
                  checks, warnings, files)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    validate_period(args.start, args.end)
    cfg, label_codes = load_config(args.config, args.codes)
    client = make_client(cfg, args.fixtures)

    print(f"Periode {args.start:%d-%m-%Y} t/m {args.end:%d-%m-%Y}", file=sys.stderr)
    print("Gegevens ophalen uit Moneybird...", file=sys.stderr)
    data = fetch_data(client, args.start, args.end)
    events_path = find_events(args.events, args.fixtures)
    if args.no_budget:
        budget = None
    elif args.budget:
        budget = load_budget(args.budget)
    else:
        budget = find_budget(args.start)
    report = build_report(cfg, label_codes, data, args.start, args.end, events_path, budget)
    if report.events_path:
        print(f"Evenementenlijst: {report.events_path}", file=sys.stderr)
    if budget:
        print(f"Begroting: {budget.path} ({budget.status})", file=sys.stderr)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    # Evenement- en begrotingstabellen van een vorige run weghalen (organizers of begroting kunnen veranderd zijn)
    for old in [*out.glob("events_*.tex"), *out.glob("budget_*.tex")]:
        if old.read_text(encoding="utf-8").startswith("% Gegenereerd door jaarverslag.py"):
            old.unlink()
    for filename, content in report.files.items():
        (out / filename).write_text(content, encoding="utf-8")

    # Samenvatting naar de terminal
    grand = report.grand
    print("\nResultaat per categorie:", file=sys.stderr)
    for r in [*report.overview_rows, Row("", "TOTAL", grand.budget, grand.actual)]:
        print(f"  {r.label:<34} kosten {fmt_plain(r.actual.debit):>12}  opbrengsten "
              f"{fmt_plain(r.actual.credit):>12}  resultaat {fmt_plain(r.actual.result):>12}"
              + (f"  begroot {fmt_plain(r.budget.result):>12}" if report.budget else ""), file=sys.stderr)
    print("\nControles:", file=sys.stderr)
    for name, status, detail in report.checks:
        print(f"  [{status:<9}] {name}: {detail}", file=sys.stderr)
    failed = [c for c in report.checks if c[1] == "AFWIJKING"]
    soft = [c for c in report.checks if c[1] == "LET OP"]
    ok = sum(1 for c in report.checks if c[1] == "OK")
    print(f"  Controles: {ok} van {len(report.checks)} in orde"
          + (f", {len(failed)} afwijking(en)" if failed else "")
          + (f", {len(soft)} let op" if soft else ""), file=sys.stderr)
    if report.warnings:
        print("\nLet op:", file=sys.stderr)
        for w in report.warnings:
            print(f"  - {w}", file=sys.stderr)
    print(f"\nTabellen geschreven naar {out.resolve()}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
