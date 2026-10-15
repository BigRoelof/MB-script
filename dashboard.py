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
import datetime as dt
import hashlib
import io
import os
import urllib.error
import zipfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

import jaarverslag as jv

BASE = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["FIXTURES"] = BASE / "tests" / "fixtures"
app.config["BUDGET_DIR"] = jv.BUDGET_DIR
app.config["CODES"] = BASE / "codes.csv"
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
    show_budget: bool = True   # begroting in tabellen en downloads (uit met begroting=0)

    def query(self, **override) -> dict:
        q = {"van": f"{self.start:%Y-%m}", "tm": f"{self.end:%Y-%m}", "bron": self.source}
        if not self.show_budget:
            q["begroting"] = "0"
        q.update(override)
        return q


def month_start(text: str | None, default: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(f"{text}-01")
    except (TypeError, ValueError):
        return default


def month_end(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


college_year = jv.college_year


def read_settings() -> Settings:
    year = college_year(dt.date.today())
    start = month_start(request.args.get("van"), dt.date(year, 9, 1))
    end = month_end(month_start(request.args.get("tm"), dt.date(year + 1, 8, 1)))
    source = request.args.get("bron")
    if source not in ("live", "testdata"):
        source = "live" if has_token() else "testdata"
    return Settings(start, end, source, request.args.get("begroting") != "0")


# --------------------------------------------------------------------------
# Gegevens
# --------------------------------------------------------------------------

def cache_key(s: Settings, cfg: dict) -> tuple:
    return (s.source, cfg.get("administration_id"), s.start, s.end)


def load(s: Settings):
    """Geeft (cfg, report, opgehaald_om). Fouten komen als SystemExit (zoals in het script)."""
    jv.validate_period(s.start, s.end)
    cfg, label_codes = jv.load_config(BASE / "config.toml", app.config["CODES"])
    fixtures = app.config["FIXTURES"] if s.source == "testdata" else None
    key = cache_key(s, cfg)
    if key not in _cache:
        client = jv.make_client(cfg, fixtures)
        _cache[key] = (dt.datetime.now(), jv.fetch_data(client, s.start, s.end))
    fetched_at, data = _cache[key]
    # De begroting wordt elke keer opnieuw gelezen: wijzigingen in de editor zijn meteen zichtbaar
    report = jv.build_report(cfg, label_codes, data, s.start, s.end,
                             jv.find_events(None, fixtures),
                             jv.find_budget(s.start, app.config["BUDGET_DIR"]) if s.show_budget else None)
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


@app.template_filter("datum")
def datum_filter(day: dt.date | None) -> str:
    return f"{day.day} {MONTHS[day.month - 1][:3]} {day.year}" if day else ""


@app.template_filter("datum_lang")
def datum_lang_filter(day: dt.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


# Voortgangsbalk: 100% van de begroting ligt op 70% van de breedte, de rest is ruimte voor overschrijding
PROGRESS_BUDGET_WIDTH = 70


@app.template_global("progress")
def budget_progress(row: jv.Row) -> dict | None:
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


def organizer_view(report: jv.Report) -> list[dict]:
    lines_by_event: dict[str, list[jv.EventLine]] = {}
    for line in report.lines:
        if line.event_id:
            lines_by_event.setdefault(line.event_id, []).append(line)
    for lines in lines_by_event.values():
        lines.sort(key=lambda l: (l.date or dt.date.min, l.reference))
    out = []
    for heading, rows in report.organizers:
        out.append({
            "heading": heading,
            "file": f"events_{jv.slug(heading)}.tex",
            "total": jv.total([jv.Row("", "", actual=r.amounts) for r in rows]).actual,
            "events": [{"row": r,
                        "lines": lines_by_event.get(r.event_id, [])} for r in rows],
        })
    return out


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
            categories=[(c, report.tables[c["digit"]], o)
                        for c, o in zip(report.categories, report.overview_rows)],
            organizers=organizer_view(report),
            events_total=jv.total([jv.Row("", "", actual=r.amounts)
                                   for _, rows in report.organizers for r in rows]).actual,
            unlabeled=[l for l in report.lines if not l.event_id],
            budget=report.budget,
            budget_year=college_year(s.start),
            budget_exists=jv.budget_path(college_year(s.start), app.config["BUDGET_DIR"]).exists(),
            budget_toggle=url_for("index", **{k: v for k, v in s.query().items() if k != "begroting"},
                                  **({} if not s.show_budget else {"begroting": "0"})) + "#categorieen",
            full_year=(s.start, s.end) == (dt.date(college_year(s.start), 9, 1),
                                           dt.date(college_year(s.start) + 1, 8, 31)),
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


# --------------------------------------------------------------------------
# Begroting
# --------------------------------------------------------------------------

def file_version(path: Path) -> str:
    """Kenmerk van de inhoud, om te merken dat het bestand ergens anders is aangepast."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16] if path.exists() else ""


def json_body() -> dict:
    """Alleen JSON van deze pagina zelf. Een andere website kan zo geen wijzigingen insturen:
    JSON van een ander domein vraagt eerst toestemming (CORS) en die geven we niet."""
    if not request.is_json:
        abort(415)
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        abort(403)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    return body


def api_error(message: str, status: int):
    return jsonify(error=message), status


def parse_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except InvalidOperation:
        raise ValueError(f"'{value}' is geen bedrag.")
    if not amount.is_finite() or abs(amount) >= Decimal("1e9"):
        raise ValueError(f"'{value}' is geen geldig bedrag.")
    return amount.quantize(Decimal("0.01"), ROUND_HALF_UP)


def year_report(year: int, source: str):
    """Realisatie van een heel collegejaar (of None met een melding)."""
    if year > college_year(dt.date.today()):
        return None, "Dit collegejaar is nog niet begonnen."
    loaded, error = load_or_error(Settings(dt.date(year, 9, 1), dt.date(year + 1, 8, 31), source))
    return (loaded[1] if loaded else None), error


@app.get("/begroting")
def budget_page():
    s = read_settings()
    current = college_year(dt.date.today())
    try:
        year = int(request.args.get("jaar", ""))
    except ValueError:
        year = college_year(s.start)
    directory = app.config["BUDGET_DIR"]
    context = {"s": s, "has_token": has_token(), "year": year, "current": current}
    try:
        cfg, _ = jv.load_config(BASE / "config.toml", app.config["CODES"])
        path = jv.budget_path(year, directory)
        budget = jv.load_budget(path, year) if path.exists() else None
    except SystemExit as err:
        context["error"] = str(err.code)
        return render_template("begroting.html", **context), 500

    report, actual_error = year_report(year, s.source) if budget else (None, None)
    actual = {r.code: r.actual for rows in report.tables.values() for r in rows} if report else {}
    amounts = budget.amounts if budget else {}
    categories = []
    for c in cfg["categories"]:
        codes = sorted({k for k in [*cfg["rows"], *amounts] if jv.category_digit(k) == c["digit"]})
        free = [f"C{c['digit']}{n:02d}" for n in range(1, 100)
                if f"C{c['digit']}{n:02d}" not in cfg["rows"] and f"C{c['digit']}{n:02d}" > max(codes, default="")]
        categories.append({"cat": c, "next_code": free[0] if free else None, "rows": [{
            "code": k,
            "name": cfg["rows"].get(k),
            "budget": amounts.get(k, jv.Amounts()),
            "note": budget.notes.get(k, "") if budget else "",
            "actual": actual.get(k),
        } for k in codes]})

    previous = year - 1
    context.update(
        cfg=cfg, budget=budget, categories=categories, version=file_version(jv.budget_path(year, directory)),
        years=sorted(set(jv.budget_years(directory)) | {current, current + 1, year}),
        existing=set(jv.budget_years(directory)),
        actual_error=actual_error, has_actual=report is not None,
        previous=previous, previous_budget=jv.budget_path(previous, directory).exists(),
        previous_actual=previous <= current,
        today=dt.date.today(),
    )
    return render_template("begroting.html", **context)


@app.post("/api/begroting/<int:year>")
def budget_create(year: int):
    """Nieuwe begroting: leeg, als kopie van vorig jaar, of gevuld met de realisatie van vorig jaar."""
    body = json_body()
    directory = app.config["BUDGET_DIR"]
    path = jv.budget_path(year, directory)
    if path.exists():
        return api_error(f"Er is al een begroting voor {jv.year_label(year)}.", 409)
    budget = jv.Budget(year)
    source = body.get("bron")
    if source == "begroting":
        prev_path = jv.budget_path(year - 1, directory)
        if not prev_path.exists():
            return api_error(f"Er is geen begroting voor {jv.year_label(year - 1)}.", 404)
        prev = jv.load_budget(prev_path, year - 1)
        budget.amounts, budget.notes = prev.amounts, prev.notes
    elif source == "realisatie":
        report, error = year_report(year - 1, body.get("databron") or read_settings().source)
        if report is None:
            return api_error(f"De realisatie van {jv.year_label(year - 1)} is niet beschikbaar: {error}", 502)
        euro = Decimal("1")
        for rows in report.tables.values():
            for r in rows:
                if not r.actual.is_zero():
                    budget.amounts[r.code] = jv.Amounts(r.actual.debit.quantize(euro, ROUND_HALF_UP),
                                                        r.actual.credit.quantize(euro, ROUND_HALF_UP))
    elif source != "leeg":
        abort(400)
    jv.save_budget(budget, path)
    return jsonify(version=file_version(path)), 201


@app.put("/api/begroting/<int:year>")
def budget_save(year: int):
    """Slaat de hele begroting op (de editor stuurt na elke wijziging alle regels)."""
    body = json_body()
    path = jv.budget_path(year, app.config["BUDGET_DIR"])
    if not path.exists():
        return api_error("Deze begroting bestaat niet (meer).", 404)
    if body.get("version") != file_version(path):
        return api_error("De begroting is ondertussen ergens anders aangepast. Herlaad de pagina.", 409)
    budget = jv.load_budget(path, year)
    if budget.locked:
        return api_error("Deze begroting is vastgesteld en kan niet meer worden aangepast.", 423)
    amounts, notes = {}, {}
    try:
        for code, row in (body.get("rows") or {}).items():
            if not jv.CODE_RE.fullmatch(code) or not isinstance(row, dict):
                raise ValueError(f"Ongeldige regel '{code}'.")
            amounts[code] = jv.Amounts(parse_amount(row.get("debit")), parse_amount(row.get("credit")))
            note = " ".join(str(row.get("note") or "").split())
            if len(note) > 300:
                raise ValueError(f"De toelichting bij {code} is te lang (maximaal 300 tekens).")
            if note:
                notes[code] = note
    except ValueError as err:
        return api_error(str(err), 400)
    budget.amounts, budget.notes = amounts, notes
    jv.save_budget(budget, path)
    return jsonify(version=file_version(path), saved=dt.datetime.now().strftime("%H:%M"))


@app.post("/api/begroting/<int:year>/status")
def budget_status(year: int):
    """Vaststellen (vergrendelen) of terugzetten naar concept."""
    body = json_body()
    path = jv.budget_path(year, app.config["BUDGET_DIR"])
    if not path.exists():
        return api_error("Deze begroting bestaat niet (meer).", 404)
    if body.get("version") != file_version(path):
        return api_error("De begroting is ondertussen ergens anders aangepast. Herlaad de pagina.", 409)
    budget = jv.load_budget(path, year)
    if body.get("status") == "vastgesteld":
        try:
            adopted = dt.date.fromisoformat(body.get("date") or "")
        except ValueError:
            return api_error("Vul de datum van de ALV in.", 400)
        budget.status, budget.adopted = "vastgesteld", adopted
    elif body.get("status") == "concept":
        budget.status, budget.adopted = "concept", None
    else:
        abort(400)
    jv.save_budget(budget, path)
    return jsonify(version=file_version(path))


def code_categories() -> list[str]:
    cfg = jv.tomllib.loads((BASE / "config.toml").read_text(encoding="utf-8"))
    return [c["digit"] for c in cfg["categories"]]


@app.post("/api/codes")
def code_add():
    body = json_body()
    try:
        jv.edit_codes(app.config["CODES"], code_categories(), str(body.get("code", "")),
                      str(body.get("name", "")), new=True)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True), 201


@app.put("/api/codes/<code>")
def code_rename(code: str):
    body = json_body()
    try:
        jv.edit_codes(app.config["CODES"], code_categories(), code, str(body.get("name", "")), new=False)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True)


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
