"""De begroting per collegejaar: begroting/<jaar>-<jaar+1>.csv lezen en schrijven."""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .amounts import CODE_RE, Amounts, dec
from .config import PROJECT_DIR


BUDGET_DIR = PROJECT_DIR / "begroting"
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
