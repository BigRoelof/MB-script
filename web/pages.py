"""De startpagina en de downloads (.tex en zip)."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, url_for

from verslag.amounts import Row, total
from verslag.budget import budget_path, college_year
from verslag.events import EventLine
from verslag.report import Report

from .common import Settings, clear_cache, has_token, load_or_error, read_settings

bp = Blueprint("pages", __name__)


def organizer_view(report: Report) -> list[dict]:
    lines_by_event: dict[str, list[EventLine]] = {}
    for line in report.lines:
        if line.event_id:
            lines_by_event.setdefault(line.event_id, []).append(line)
    for lines in lines_by_event.values():
        lines.sort(key=lambda l: (l.date or dt.date.min, l.reference))
    out = []
    for org in report.organizers:
        out.append({
            "heading": org.heading,
            "total": total([Row("", "", actual=r.amounts) for r in org.rows]).actual,
            "events": [{"row": r,
                        "lines": lines_by_event.get(r.event_id, [])} for r in org.rows],
        })
    return out


@bp.get("/")
def index():
    s = read_settings()
    loaded, error = load_or_error(s)
    context = {"s": s, "error": error, "has_token": has_token()}
    if loaded:
        cfg, report, fetched_at = loaded
        checks_ok = sum(1 for c in report.checks if c[1] == "OK")
        year = college_year(s.start)
        context.update(
            cfg=cfg, report=report, fetched_at=fetched_at,
            categories=[(c, report.tables[c["digit"]], o)
                        for c, o in zip(report.categories, report.overview_rows)],
            organizers=organizer_view(report),
            events_total=total([Row("", "", actual=r.amounts)
                                for org in report.organizers for r in org.rows]).actual,
            unlabeled=[l for l in report.lines if not l.event_id],
            budget=report.budget,
            budget_year=year,
            budget_exists=budget_path(year, current_app.config["BUDGET_DIR"]).exists(),
            budget_toggle=url_for("pages.index", **{k: v for k, v in s.query().items() if k != "begroting"},
                                  **({} if not s.show_budget else {"begroting": "0"})) + "#categorieen",
            full_year=(s.start, s.end) == (dt.date(year, 9, 1), dt.date(year + 1, 8, 31)),
            checks_ok=checks_ok,
            checks_failed=[c for c in report.checks if c[1] == "AFWIJKING"],
            checks_soft=[c for c in report.checks if c[1] == "LET OP"],
        )
    return render_template("index.html", **context), (200 if loaded else 500)


@bp.post("/vernieuwen")
def refresh():
    s = read_settings()
    clear_cache(s)
    # Terug naar de pagina waar het menu werd gebruikt (alleen een pad op deze site)
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("pages.index", **s.query())
    return redirect(target)


def loaded_report():
    s = read_settings()
    loaded, error = load_or_error(s)
    if error:
        abort(500, error)
    cfg, report, _ = loaded
    return s, cfg, report


def period_tag(s: Settings) -> str:
    return f"{s.start:%Y-%m}_{s.end:%Y-%m}"


@bp.get("/tex/<name>")
def tex_file(name: str):
    _, _, report = loaded_report()
    if name not in report.files:
        abort(404)
    return Response(report.files[name], mimetype="text/x-tex",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/zip")
def zip_file():
    s, _, report = loaded_report()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in report.files.items():
            z.writestr(name, content)
    return Response(buffer.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="jaarverslag-tabellen_{period_tag(s)}.zip"'})
