"""Instellingen: config.toml, codes.csv (lezen en bewerken) en de periode."""

from __future__ import annotations

import calendar
import csv
import io
import os
import tomllib
import datetime as dt
from pathlib import Path

from .amounts import CODE_RE, NUMBER_FMT, category_digit


# De projectmap: hier staan config.toml, codes.csv, events.csv, .env en begroting/
PROJECT_DIR = Path(__file__).parent.parent


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


def validate_period(start: dt.date, end: dt.date):
    last_day = calendar.monthrange(end.year, end.month)[1]
    if start.day != 1 or end.day != last_day:
        raise SystemExit("Moneybird-rapporten werken met hele maanden: --start moet de 1e van een maand "
                         "zijn en --end de laatste dag van een maand.")
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if months < 1 or months > 12:
        raise SystemExit("De periode moet tussen 1 en 12 maanden lang zijn.")


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
