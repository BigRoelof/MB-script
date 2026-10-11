#!/usr/bin/env python3
"""Dashboard voor de jaarverslag-tabellen van Thalia.

Toont alles wat jaarverslag.py uitrekent op één pagina, met downloadknoppen voor de
LaTeX-tabellen. Draait alleen op je eigen computer (127.0.0.1).

Gebruik:
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # eenmalig
    cp .env.example .env && chmod 600 .env   # token invullen, alleen nodig voor live gegevens
    .venv/bin/python dashboard.py     # daarna naar http://127.0.0.1:5000

Zonder token kun je de testdata uit tests/fixtures bekijken.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import io
import math
import os
import urllib.error
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, url_for

import jaarverslag as jv

BASE = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["FIXTURES"] = BASE / "tests" / "fixtures"
app.config["TEMPLATES_AUTO_RELOAD"] = True  # aanpassingen aan de template meteen zichtbaar

# Opgehaalde Moneybird-gegevens per (bron, administratie, periode): (tijdstip, data).
# De live API is traag en heeft een rate limit; "Opnieuw ophalen" leegt de cache.
_cache: dict[tuple, tuple[dt.datetime, dict]] = {}

MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
          "september", "oktober", "november", "december"]


# --------------------------------------------------------------------------
# Instellingen uit de URL
# --------------------------------------------------------------------------

def has_token() -> bool:
    return bool(os.environ.get("MONEYBIRD_TOKEN"))


@dataclass
class Settings:
    start: dt.date
    end: dt.date
    source: str      # "live" of "testdata"
    drafts: bool     # conceptfacturen en -offertes meetellen bij partners

    def query(self, **override) -> dict:
        q = {"van": f"{self.start:%Y-%m}", "tm": f"{self.end:%Y-%m}", "bron": self.source}
        if self.drafts:
            q["concepten"] = "1"
        q.update(override)
        return q


def month_start(text: str | None, default: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(f"{text}-01")
    except (TypeError, ValueError):
        return default


def month_end(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def college_year(day: dt.date) -> int:
    return day.year if day.month >= 9 else day.year - 1


def read_settings() -> Settings:
    year = college_year(dt.date.today())
    start = month_start(request.args.get("van"), dt.date(year, 9, 1))
    end = month_end(month_start(request.args.get("tm"), dt.date(year + 1, 8, 1)))
    source = request.args.get("bron")
    if source not in ("live", "testdata"):
        source = "live" if has_token() else "testdata"
    return Settings(start, end, source, request.args.get("concepten") == "1")


# --------------------------------------------------------------------------
# Gegevens
# --------------------------------------------------------------------------

def cache_key(s: Settings, cfg: dict) -> tuple:
    return (s.source, cfg.get("administration_id"), s.start, s.end)


def load(s: Settings):
    """Geeft (cfg, report, opgehaald_om). Fouten komen als SystemExit (zoals in het script)."""
    jv.validate_period(s.start, s.end)
    cfg, label_codes = jv.load_config(BASE / "config.toml", BASE / "codes.csv")
    fixtures = app.config["FIXTURES"] if s.source == "testdata" else None
    key = cache_key(s, cfg)
    if key not in _cache:
        client = jv.make_client(cfg, fixtures)
        _cache[key] = (dt.datetime.now(), jv.fetch_data(client, s.start, s.end))
    fetched_at, data = _cache[key]
    report = jv.build_report(cfg, label_codes, data, s.start, s.end,
                             jv.find_events(None, fixtures), include_drafts=s.drafts)
    return cfg, report, fetched_at


def load_or_error(s: Settings):
    try:
        return load(s), None
    except SystemExit as err:
        return None, str(err.code)
    except urllib.error.URLError as err:
        return None, f"Moneybird is niet bereikbaar ({err.reason}). Controleer je internetverbinding."


# --------------------------------------------------------------------------
# Opmaak
# --------------------------------------------------------------------------

def nl_number(value: Decimal) -> str:
    q = Decimal(value).quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return ("−" if q < 0 else "") + s


@app.template_filter("num")
def num_filter(value) -> str:
    return nl_number(value)


@app.template_filter("eur")
def eur_filter(value) -> str:
    s = nl_number(value)
    return f"−€ {s[1:]}" if s.startswith("−") else f"€ {s}"


@app.template_filter("geheel")
def geheel_filter(value) -> str:
    return f"{int(value):,}".replace(",", ".")


@app.template_filter("datum")
def datum_filter(day: dt.date | None) -> str:
    return f"{day.day} {MONTHS[day.month - 1][:3]} {day.year}" if day else ""


@app.template_filter("datum_lang")
def datum_lang_filter(day: dt.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def nice_axis(maximum: float, ticks: int = 4) -> list[float]:
    """Ronde asstappen van 0 tot net boven het maximum (1, 2 of 5 maal een macht van 10)."""
    if maximum <= 0:
        return [0.0, 1.0]
    raw = maximum / ticks
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    count = math.ceil(maximum / step)
    return [i * step for i in range(count + 1)]


def category_chart(report: jv.Report) -> dict:
    rows = report.overview_rows
    axis = nice_axis(float(max([r.actual.debit for r in rows] + [r.actual.credit for r in rows], default=0)))
    top = axis[-1]
    return {
        "axis": [{"value": Decimal(str(v)), "pct": v / top * 100} for v in axis],
        "rows": [{"row": r,
                  "credit_pct": float(r.actual.credit) / top * 100,
                  "debit_pct": float(r.actual.debit) / top * 100} for r in rows],
    }


def result_bar(amounts: jv.Amounts, scale: float) -> dict:
    """Staafje rond nul voor het resultaat van een evenement (verlies links, winst rechts)."""
    result = float(amounts.result)
    return {"pct": abs(result) / scale * 50 if scale else 0, "side": "neg" if result < 0 else "pos"}


def organizer_view(report: jv.Report) -> list[dict]:
    lines_by_event: dict[str, list[jv.EventLine]] = {}
    for line in report.lines:
        if line.event_id:
            lines_by_event.setdefault(line.event_id, []).append(line)
    for lines in lines_by_event.values():
        lines.sort(key=lambda l: (l.date or dt.date.min, l.reference))
    scale = max((abs(float(r.amounts.result)) for _, rows in report.organizers for r in rows), default=0)
    out = []
    for heading, rows in report.organizers:
        out.append({
            "heading": heading,
            "file": f"events_{jv.slug(heading)}.tex",
            "total": jv.total([jv.Row("", "", actual=r.amounts) for r in rows]).actual,
            "events": [{"row": r, "bar": result_bar(r.amounts, scale),
                        "lines": lines_by_event.get(r.event_id, [])} for r in rows],
        })
    return out


def balance_view(report: jv.Report) -> dict:
    a0, l0, ta0, tl0 = report.balance_start
    a1, l1, ta1, tl1 = report.balance_end

    def side(begin, end):
        return [{"name": k, "begin": begin.get(k), "end": end.get(k)} for k in dict.fromkeys([*begin, *end])]

    return {"begin": report.start - dt.timedelta(days=1), "end": report.end,
            "assets": side(a0, a1), "liabilities": side(l0, l1),
            "totals": {"assets": (ta0, ta1), "liabilities": (tl0, tl1)}}


def presets(s: Settings) -> list[dict]:
    year = college_year(s.start)
    options = [
        (f"{year}–{year + 1}", dt.date(year, 9, 1), dt.date(year + 1, 8, 31)),
        (f"Halfjaar {year}–{year + 1}", dt.date(year, 9, 1), dt.date(year + 1, 1, 31)),
        (f"{year - 1}–{year}", dt.date(year - 1, 9, 1), dt.date(year, 8, 31)),
    ]
    return [{"label": label, "active": (start, end) == (s.start, s.end),
             "url": url_for("index", **s.query(van=f"{start:%Y-%m}", tm=f"{end:%Y-%m}"))}
            for label, start, end in options]


# --------------------------------------------------------------------------
# Pagina's en downloads
# --------------------------------------------------------------------------

@app.get("/")
def index():
    s = read_settings()
    loaded, error = load_or_error(s)
    context = {"s": s, "error": error, "has_token": has_token(), "presets": presets(s)}
    if loaded:
        cfg, report, fetched_at = loaded
        checks_ok = sum(1 for c in report.checks if c[1] == "OK")
        context.update(
            cfg=cfg, report=report, fetched_at=fetched_at,
            grand=report.grand.actual,
            chart=category_chart(report),
            categories=[(c, report.tables[c["digit"]], o)
                        for c, o in zip(report.categories, report.overview_rows)],
            organizers=organizer_view(report),
            unlabeled=[l for l in report.lines if not l.event_id],
            balance=balance_view(report),
            partner_totals={k: sum((a for _, a in v), Decimal(0)) for k, v in report.partners.items()},
            checks_ok=checks_ok,
            checks_failed=[c for c in report.checks if c[1] == "AFWIJKING"],
            checks_soft=[c for c in report.checks if c[1] == "LET OP"],
        )
    return render_template("index.html", **context), (200 if loaded else 500)


@app.post("/vernieuwen")
def refresh():
    s = read_settings()
    for key in [k for k in _cache if k[0] == s.source and k[2:] == (s.start, s.end)]:
        del _cache[key]
    return redirect(url_for("index", **s.query()))


def loaded_report():
    s = read_settings()
    loaded, error = load_or_error(s)
    if error:
        abort(500, error)
    cfg, report, _ = loaded
    return s, cfg, report


def period_tag(s: Settings) -> str:
    return f"{s.start:%Y-%m}_{s.end:%Y-%m}"


@app.get("/tex/<name>")
def tex_file(name: str):
    _, _, report = loaded_report()
    if name not in report.files:
        abort(404)
    return Response(report.files[name], mimetype="text/x-tex",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/zip")
def zip_file():
    s, _, report = loaded_report()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in report.files.items():
            z.writestr(name, content)
    return Response(buffer.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="jaarverslag-tabellen_{period_tag(s)}.zip"'})


def excel_amount(value: Decimal) -> str:
    return f"{Decimal(value):.2f}".replace(".", ",")


@app.get("/csv/boekregels")
def lines_csv():
    """Alle boekregels met een evenementlabel of onder een labelcode, voor Excel (; en komma)."""
    s, cfg, report = loaded_report()
    names = cfg.get("events", {}).get("organizers", {})
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["event", "evenement", "organizer", "code", "datum", "document", "omschrijving",
                     "kosten", "opbrengsten"])
    for l in sorted(report.lines, key=lambda l: (l.event_id or "~", l.date or dt.date.min)):
        event = report.events.get(l.event_id)
        writer.writerow([f"E{l.event_id}" if l.event_id else "", event.name if event else "",
                         names.get(event.organizer, event.organizer) if event else "",
                         l.code, f"{l.date:%d-%m-%Y}" if l.date else "", l.reference, l.description,
                         excel_amount(l.amounts.debit), excel_amount(l.amounts.credit)])
    return Response("﻿" + buffer.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="boekregels_{period_tag(s)}.csv"'})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--fixtures", type=Path, default=app.config["FIXTURES"],
                   help="map met testdata (standaard tests/fixtures)")
    p.add_argument("--debug", action="store_true", help="herladen bij codewijzigingen")
    args = p.parse_args()
    jv.load_dotenv()
    app.config["FIXTURES"] = args.fixtures
    # Alleen lokaal bereikbaar: het dashboard toont financiële gegevens zonder inlog
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
