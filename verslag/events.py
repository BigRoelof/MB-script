"""Evenementen: labels [E…] in de boekregels, events.csv uit concrexit en de indeling per organizer."""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from .amounts import CODE_RE, TOLERANCE, ZERO, Amounts, dec, fmt_plain
from .config import PROJECT_DIR
from .errors import VerslagError


# Evenementlabel in de omschrijving van een boekregel: [E1234], met het event-id uit concrexit
LABEL_RE = re.compile(r"\[E(\d+)\]", re.IGNORECASE)
# Kaal label in de documentnaam, zoals de penningmeester het typt: "2627017 E1206", "F2627001 E1205".
# Alleen als los woord met hoofdletter E, dus niet midden in een concrexit-uuid ("542cdaa0-e123-...").
BARE_LABEL_RE = re.compile(r"(?<!\S)E(\d+)(?!\S)")
# Boekingsdatum en evenementdatum mogen standaard zoveel dagen uit elkaar liggen
DEFAULT_MAX_DAYS = 60
COST_LEDGER_TYPES = {"expenses", "direct_costs", "other_income_expenses"}


@dataclass
class Event:
    name: str
    date: dt.date
    organizer: str
    other_organizers: tuple[str, ...] = ()   # concrexit kent meer organisers per event; de eerste telt


@dataclass
class EventList:
    """De evenementen met naam, datum en organizer, uit events.csv of van thalia.nu."""
    events: dict[str, Event]
    source: str                          # waar de lijst vandaan komt; "" = geen lijst
    warnings: list[str] = field(default_factory=list)


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
            raise VerslagError(f"{path.name}: '{rec.get('id')}' is geen geldig event-id (verwacht een getal).")
        if event_id in events:
            raise VerslagError(f"{path.name}: event {event_id} staat er twee keer in.")
        try:
            date = parse_date(rec.get("date") or "")
        except ValueError:
            raise VerslagError(f"{path.name}: event {event_id} heeft geen geldige datum ('{rec.get('date')}').")
        organizer = (rec.get("organizer") or "").strip() or "Other"
        events[event_id] = Event(name or f"Event {event_id}", date, organizer)
    return events


def events_from_csv(path: Path) -> EventList:
    """events.csv inlezen; ontbreekt het bestand, dan een lege lijst met een waarschuwing."""
    if path.exists():
        return EventList(load_events(path), str(path))
    return EventList({}, "", [f"Evenementenlijst {path} niet gevonden: de evenementtabellen tonen alleen "
                              "nummers en de labels worden niet gecontroleerd."])


def find_events(events_arg: Path | None, fixtures: Path | None) -> Path:
    """events.csv: --events, anders naast het script, anders in de fixturesmap."""
    if events_arg is not None:
        return events_arg
    candidates = [PROJECT_DIR / "events.csv"]
    if fixtures:
        candidates.append(fixtures / "events.csv")
    return next((c for c in candidates if c.exists()), candidates[0])


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


def organizer_groups(lines: list[EventLine], events: dict[str, Event], start: dt.date, end: dt.date,
                     organizer_names: dict[str, str]) -> list[tuple[str, list[EventRow]]]:
    """Per organizer zijn evenementen: (kop, [EventRow]).

    Een evenement komt erin als het in de periode valt of als er in de periode op geboekt is.
    Volgorde van de organizers: zoals ze voor het eerst in de evenementenlijst staan.
    Er wordt gegroepeerd op de nette naam uit [events.organizers]: twee organizers met dezelfde
    naam komen in één tabel. Labels die niet in de lijst staan komen in een aparte tabel onderaan.
    """
    per_event: dict[str, Amounts] = {}
    for l in lines:
        if l.event_id:
            per_event.setdefault(l.event_id, Amounts())
            per_event[l.event_id] += l.amounts

    by_organizer: dict[str, list[str]] = {}
    for event_id, e in events.items():
        if event_id in per_event or start <= e.date <= end:
            by_organizer.setdefault(organizer_names.get(e.organizer, e.organizer), []).append(event_id)

    result = []
    for heading, ids in by_organizer.items():
        ids.sort(key=lambda i: (events[i].date, int(i)))
        rows = [EventRow(i, events[i].name, events[i].date, per_event.get(i, Amounts())) for i in ids]
        result.append((heading, rows))

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
