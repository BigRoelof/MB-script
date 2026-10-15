"""De begrotingseditor (/begroting) en de API waarmee die opslaat, plus codes toevoegen/hernoemen."""

from __future__ import annotations

import datetime as dt
import hashlib
import tomllib
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from verslag.amounts import CODE_RE, Amounts, category_digit
from verslag.budget import Budget, budget_path, budget_years, college_year, load_budget, save_budget, year_label
from verslag.config import PROJECT_DIR, edit_codes

from .common import Settings, has_token, load_cfg, load_or_error, read_settings

bp = Blueprint("begroting", __name__)


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


@bp.get("/begroting")
def budget_page():
    s = read_settings()
    current = college_year(dt.date.today())
    try:
        year = int(request.args.get("jaar", ""))
    except ValueError:
        year = college_year(s.start)
    directory = current_app.config["BUDGET_DIR"]
    context = {"s": s, "has_token": has_token(), "year": year, "current": current}
    try:
        cfg, _ = load_cfg()
        path = budget_path(year, directory)
        budget = load_budget(path, year) if path.exists() else None
    except SystemExit as err:
        context["error"] = str(err.code)
        return render_template("begroting.html", **context), 500

    report, actual_error = year_report(year, s.source) if budget else (None, None)
    actual = {r.code: r.actual for rows in report.tables.values() for r in rows} if report else {}
    amounts = budget.amounts if budget else {}
    categories = []
    for c in cfg["categories"]:
        codes = sorted({k for k in [*cfg["rows"], *amounts] if category_digit(k) == c["digit"]})
        free = [f"C{c['digit']}{n:02d}" for n in range(1, 100)
                if f"C{c['digit']}{n:02d}" not in cfg["rows"] and f"C{c['digit']}{n:02d}" > max(codes, default="")]
        categories.append({"cat": c, "next_code": free[0] if free else None, "rows": [{
            "code": k,
            "name": cfg["rows"].get(k),
            "budget": amounts.get(k, Amounts()),
            "note": budget.notes.get(k, "") if budget else "",
            "actual": actual.get(k),
        } for k in codes]})

    previous = year - 1
    context.update(
        cfg=cfg, budget=budget, categories=categories, version=file_version(budget_path(year, directory)),
        years=sorted(set(budget_years(directory)) | {current, current + 1, year}),
        existing=set(budget_years(directory)),
        actual_error=actual_error, has_actual=report is not None,
        previous=previous, previous_budget=budget_path(previous, directory).exists(),
        previous_actual=previous <= current,
        today=dt.date.today(),
    )
    return render_template("begroting.html", **context)


@bp.post("/api/begroting/<int:year>")
def budget_create(year: int):
    """Nieuwe begroting: leeg, als kopie van vorig jaar, of gevuld met de realisatie van vorig jaar."""
    body = json_body()
    directory = current_app.config["BUDGET_DIR"]
    path = budget_path(year, directory)
    if path.exists():
        return api_error(f"Er is al een begroting voor {year_label(year)}.", 409)
    budget = Budget(year)
    source = body.get("bron")
    if source == "begroting":
        prev_path = budget_path(year - 1, directory)
        if not prev_path.exists():
            return api_error(f"Er is geen begroting voor {year_label(year - 1)}.", 404)
        prev = load_budget(prev_path, year - 1)
        budget.amounts, budget.notes = prev.amounts, prev.notes
    elif source == "realisatie":
        report, error = year_report(year - 1, body.get("databron") or read_settings().source)
        if report is None:
            return api_error(f"De realisatie van {year_label(year - 1)} is niet beschikbaar: {error}", 502)
        euro = Decimal("1")
        for rows in report.tables.values():
            for r in rows:
                if not r.actual.is_zero():
                    budget.amounts[r.code] = Amounts(r.actual.debit.quantize(euro, ROUND_HALF_UP),
                                                        r.actual.credit.quantize(euro, ROUND_HALF_UP))
    elif source != "leeg":
        abort(400)
    save_budget(budget, path)
    return jsonify(version=file_version(path)), 201


@bp.put("/api/begroting/<int:year>")
def budget_save(year: int):
    """Slaat de hele begroting op (de editor stuurt na elke wijziging alle regels)."""
    body = json_body()
    path = budget_path(year, current_app.config["BUDGET_DIR"])
    if not path.exists():
        return api_error("Deze begroting bestaat niet (meer).", 404)
    if body.get("version") != file_version(path):
        return api_error("De begroting is ondertussen ergens anders aangepast. Herlaad de pagina.", 409)
    budget = load_budget(path, year)
    if budget.locked:
        return api_error("Deze begroting is vastgesteld en kan niet meer worden aangepast.", 423)
    amounts, notes = {}, {}
    try:
        for code, row in (body.get("rows") or {}).items():
            if not CODE_RE.fullmatch(code) or not isinstance(row, dict):
                raise ValueError(f"Ongeldige regel '{code}'.")
            amounts[code] = Amounts(parse_amount(row.get("debit")), parse_amount(row.get("credit")))
            note = " ".join(str(row.get("note") or "").split())
            if len(note) > 300:
                raise ValueError(f"De toelichting bij {code} is te lang (maximaal 300 tekens).")
            if note:
                notes[code] = note
    except ValueError as err:
        return api_error(str(err), 400)
    budget.amounts, budget.notes = amounts, notes
    save_budget(budget, path)
    return jsonify(version=file_version(path), saved=dt.datetime.now().strftime("%H:%M"))


@bp.post("/api/begroting/<int:year>/status")
def budget_status(year: int):
    """Vaststellen (vergrendelen) of terugzetten naar concept."""
    body = json_body()
    path = budget_path(year, current_app.config["BUDGET_DIR"])
    if not path.exists():
        return api_error("Deze begroting bestaat niet (meer).", 404)
    if body.get("version") != file_version(path):
        return api_error("De begroting is ondertussen ergens anders aangepast. Herlaad de pagina.", 409)
    budget = load_budget(path, year)
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
    save_budget(budget, path)
    return jsonify(version=file_version(path))


def code_categories() -> list[str]:
    cfg = tomllib.loads((PROJECT_DIR / "config.toml").read_text(encoding="utf-8"))
    return [c["digit"] for c in cfg["categories"]]


@bp.post("/api/codes")
def code_add():
    body = json_body()
    try:
        edit_codes(current_app.config["CODES"], code_categories(), str(body.get("code", "")),
                      str(body.get("name", "")), new=True)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True), 201


@bp.put("/api/codes/<code>")
def code_rename(code: str):
    body = json_body()
    try:
        edit_codes(current_app.config["CODES"], code_categories(), code, str(body.get("name", "")), new=False)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True)
