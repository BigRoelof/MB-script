"""Codes beheren (/codes): alle C-codes met naam, de Moneybird-projecten erbij en waar ze gebruikt worden."""

from __future__ import annotations

import re

from flask import Blueprint, current_app, jsonify, render_template

from verslag.amounts import CODE_RE, category_digit, dec
from verslag.budget import budget_path, budget_years, load_budget
from verslag.config import delete_code, edit_codes, set_label_code

from .common import api_error, has_token, json_body, load_cfg, load_data, read_settings

bp = Blueprint("codes", __name__)


def code_usage(cfg: dict, s) -> tuple[dict, set, dict, str | None]:
    """Waar de codes gebruikt worden: (projecten, geboekt, begrotingen, fout).

    - projecten: {code: [{name, archived}]}, de projecten in Moneybird die met de code beginnen
    - geboekt: de codes met kosten of opbrengsten in de gekozen periode (uit de projectrapporten
      die het dashboard toch al ophaalt, dus zonder extra requests)
    - begrotingen: {code: ["2026–27", ...]}, alleen om verwijderen te voorkomen
    Lukt het ophalen uit Moneybird niet, dan is de fout gezet en zijn projecten en geboekt leeg.
    """
    projects: dict[str, list[dict]] = {}
    booked: set[str] = set()
    error = None
    try:
        _, data = load_data(s, cfg)
        code_of = {}
        for p in data["projects"]:
            if m := CODE_RE.match(p["name"]):
                code = m.group(1).upper()
                code_of[p["id"]] = code
                projects.setdefault(code, []).append({"name": p["name"], "archived": p.get("state") == "archived"})
        booked = {code_of[r["project_id"]] for r in data["revenue"] + data["expenses"]
                  if r.get("project_id") in code_of and dec(r["amount"]) != 0}
    except SystemExit as err:
        error = str(err.code)
    budgets: dict[str, list[str]] = {}
    directory = current_app.config["BUDGET_DIR"]
    for year in budget_years(directory):
        budget = load_budget(budget_path(year, directory), year)
        for code, amounts in budget.amounts.items():
            if not amounts.is_zero():
                budgets.setdefault(code, []).append(short_year(year))
    return projects, booked, budgets, error


def short_year(year: int) -> str:
    return f"{year}–{(year + 1) % 100:02d}"   # 2026–27


@bp.get("/codes")
def codes_page():
    s = read_settings()
    context = {"s": s, "has_token": has_token()}
    try:
        cfg, label_codes = load_cfg()
    except SystemExit as err:
        context["error"] = str(err.code)
        return render_template("codes.html", **context), 500
    projects, booked, budgets, projects_error = code_usage(cfg, s)
    categories = []
    for c in cfg["categories"]:
        codes = sorted(k for k in cfg["rows"] if category_digit(k) == c["digit"])
        free = [f"C{c['digit']}{n:02d}" for n in range(1, 100) if f"C{c['digit']}{n:02d}" not in cfg["rows"]]
        later = [k for k in free if k > max(codes, default="")]
        categories.append({"cat": c, "next_code": (later or free or [None])[0], "rows": [{
            "code": k,
            "name": cfg["rows"][k],
            "projects": projects.get(k, []),
            "booked": k in booked,
            "label": k in label_codes,
            "deletable": not projects_error and k not in projects and k not in budgets and k not in label_codes,
        } for k in codes]})
    orphans = sorted(k for k in projects if k not in cfg["rows"])   # projecten met een onbekende code
    context.update(cfg=cfg, categories=categories, projects_error=projects_error,
                   orphans=[(k, projects[k]) for k in orphans],
                   total=len(cfg["rows"]))
    return render_template("codes.html", **context)


def category_digits() -> list[str]:
    cfg, _ = load_cfg()
    return [c["digit"] for c in cfg["categories"]]


@bp.post("/api/codes")
def code_add():
    body = json_body()
    try:
        edit_codes(current_app.config["CODES"], category_digits(), str(body.get("code", "")),
                   str(body.get("name", "")), new=True)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True), 201


@bp.put("/api/codes/<code>")
def code_rename(code: str):
    body = json_body()
    try:
        edit_codes(current_app.config["CODES"], category_digits(), code, str(body.get("name", "")), new=False)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True)


@bp.put("/api/codes/<code>/label")
def code_label(code: str):
    """Evenementlabel verplicht aan/uit: [events] label_codes in config.toml."""
    body = json_body()
    if not isinstance(body.get("required"), bool):
        return api_error("Geef aan of het label verplicht is (true of false).", 400)
    try:
        set_label_code(current_app.config["CONFIG"], current_app.config["CODES"], code, body["required"])
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True)


@bp.delete("/api/codes/<code>")
def code_delete(code: str):
    """Verwijderen mag alleen als de code nergens gebruikt wordt (dat controleren we hier opnieuw)."""
    json_body()
    code = code.upper()
    if not re.fullmatch(r"C\d{3}", code):
        return api_error(f"'{code}' is geen geldige code.", 400)
    cfg, label_codes = load_cfg()
    projects, _, budgets, error = code_usage(cfg, read_settings())
    if error:
        return api_error(f"Kan niet controleren of er een project in Moneybird is: {error}", 502)
    if code in projects:
        return api_error(f"{code} heeft nog een project in Moneybird ({projects[code][0]['name']}).", 409)
    if code in budgets:
        return api_error(f"{code} staat in de begroting van {', '.join(budgets[code])}.", 409)
    if code in label_codes:
        return api_error(f"Bij {code} is het evenementlabel verplicht; zet dat eerst uit.", 409)
    try:
        delete_code(current_app.config["CODES"], category_digits(), code)
    except ValueError as err:
        return api_error(str(err), 400)
    return jsonify(ok=True)
