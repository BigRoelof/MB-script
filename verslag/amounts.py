"""Bedragen, tabelregels en de getalnotatie die overal gebruikt wordt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal


CODE_RE = re.compile(r"^\s*(C\d{3})\b", re.IGNORECASE)


ZERO = Decimal("0")
TOLERANCE = Decimal("0.01")
# Getalnotatie in de uitvoer; load_config zet dit uit [layout] in config.toml
@dataclass(frozen=True)
class NumberFormat:
    """Getalnotatie in de LaTeX-tabellen ([layout] in config.toml). Meldingen gebruiken altijd de standaard."""
    thousands: str = ","
    decimal: str = "."


DEFAULT_FORMAT = NumberFormat()


def category_digit(code: str) -> str:
    """C201 -> '2': het cijfer na de C bepaalt de categorie."""
    return code[1]


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


def total(rows: list[Row], label="TOTAL") -> Row:
    t = Row("", label)
    for r in rows:
        t.budget += r.budget
        t.actual += r.actual
    return t


def fmt_plain(value: Decimal, number_format: NumberFormat = DEFAULT_FORMAT) -> str:
    q = value.quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}"
    s = s.replace(",", "\0").replace(".", number_format.decimal).replace("\0", number_format.thousands)
    return ("-" if q < 0 else "") + s
