"""Opmaak in de templates: bedragen en datums in Nederlandse notatie, en de voortgangsbalk."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from flask import Flask

from verslag.amounts import Row

MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
          "september", "oktober", "november", "december"]

# Voortgangsbalk: 100% van de begroting ligt op 70% van de breedte, de rest is ruimte voor overschrijding
PROGRESS_BUDGET_WIDTH = 70


def nl_number(value: Decimal) -> str:
    q = Decimal(value).quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return ("−" if q < 0 else "") + s


def num_filter(value) -> str:
    return nl_number(value)


def eur_filter(value) -> str:
    s = nl_number(value)
    nbsp = "\u00a0"   # harde spatie: € en bedrag nooit over twee regels
    return f"−€{nbsp}{s[1:]}" if s.startswith("−") else f"€{nbsp}{s}"


def datum_filter(day: dt.date | None) -> str:
    return f"{day.day} {MONTHS[day.month - 1][:3]} {day.year}" if day else ""


def datum_lang_filter(day: dt.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def budget_progress(row: Row) -> dict | None:
    """Resultaat tegenover het begrote resultaat van een regel, voor de voortgangsbalk.

    Kostenpost (begroot verlies): rood als het verlies groter wordt dan begroot.
    Opbrengstpost (begrote winst): meer dan 100% is goed nieuws en blijft blauw.
    Staat het resultaat aan de andere kant van nul dan begroot, dan geen percentage maar een woord:
    "verlies" (rood) bij een opbrengstpost, "winst" bij een kostenpost.
    """
    budget, actual = row.budget.result, row.actual.result
    if budget == 0:
        return {"kind": "unbudgeted", "over": actual < 0} if actual != 0 else None
    share = float(actual / budget)
    shown = max(share, 0.0)
    room = (100 - PROGRESS_BUDGET_WIDTH) / PROGRESS_BUDGET_WIDTH   # zichtbare overschrijding (ca. 43%)
    return {
        "kind": "cost" if budget < 0 else "income",
        "pct": round(share * 100),
        "fill": min(shown, 1) * PROGRESS_BUDGET_WIDTH,
        "excess": min(max(shown - 1, 0), room) * PROGRESS_BUDGET_WIDTH,
        "clipped": shown - 1 > room,
        "over": (budget < 0 and share > 1) or (budget > 0 and actual < 0),
        "flip": ("winst" if budget < 0 else "verlies") if share < 0 else None,
    }


def register(app: Flask) -> None:
    app.add_template_filter(num_filter, "num")
    app.add_template_filter(eur_filter, "eur")
    app.add_template_filter(datum_filter, "datum")
    app.add_template_filter(datum_lang_filter, "datum_lang")
    app.add_template_global(budget_progress, "progress")
