"""Voegt alles samen: van de opgehaalde Moneybird-data naar één Report met de LaTeX-bestanden."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .amounts import Row, total
from .budget import Budget, college_year
from .events import (DEFAULT_MAX_DAYS, Event, EventLine, EventRow, event_checks, event_lines,
                     event_warnings, load_events, organizer_groups, slug)
from .latex import EXAMPLE_DOC, budget_table, event_table, results_table, tex
from .results import build_results, run_checks


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
