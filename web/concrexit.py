"""Koppelen met thalia.nu, zodat de evenementen daarvandaan komen in plaats van uit events.csv."""

from __future__ import annotations

import os

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from verslag.concrexit import Login, concrexit_settings, finish_login, is_connected, logout, pasted_params, start_login
from verslag.errors import VerslagError

from .common import clear_events_cache, has_token, load_cfg, read_settings, same_origin

bp = Blueprint("concrexit", __name__)

# Begonnen inlogpogingen, op state. Het dashboard draait voor één gebruiker op 127.0.0.1;
# na een herstart begin je gewoon opnieuw.
_logins: dict[str, Login] = {}


def render(error: str | None = None, status: int = 200):
    s = read_settings()
    context = {"s": s, "has_token": has_token(), "error": error, "settings": None,
               "connected": is_connected(current_app.config["CONCREXIT_TOKENS"]),
               "env_token": bool(os.environ.get("CONCREXIT_TOKEN")),
               "just_connected": request.args.get("gekoppeld") == "1"}
    try:
        cfg, _ = load_cfg()
        context["settings"] = concrexit_settings(cfg)
    except VerslagError as err:
        context["error"] = str(err)
        status = 500
    return render_template("concrexit.html", **context), status


@bp.get("/concrexit")
def concrexit_page():
    return render()


@bp.post("/concrexit/start")
def start():
    """Stap 1: naar de inlogpagina van thalia.nu (het formulier opent die in een nieuw tabblad)."""
    same_origin()
    try:
        cfg, _ = load_cfg()
        login = start_login(concrexit_settings(cfg))
    except VerslagError as err:
        return render(str(err), 400)
    _logins[login.state] = login
    return redirect(login.url)


@bp.post("/concrexit/koppelen")
def connect():
    """Stap 2: het geplakte adres met de code inruilen voor tokens."""
    same_origin()
    pasted = request.form.get("adres", "")
    # Onbekende state: finish_login meldt dan dat het adres bij een andere inlogpoging hoort
    login = _logins.get(pasted_params(pasted).get("state", ""), Login("", "-", ""))
    try:
        cfg, _ = load_cfg()
        finish_login(concrexit_settings(cfg), login, pasted, current_app.config["CONCREXIT_TOKENS"])
    except VerslagError as err:
        return render(str(err), 400)
    _logins.clear()
    clear_events_cache()
    return redirect(url_for("concrexit.concrexit_page", gekoppeld="1", **read_settings().query()))


@bp.post("/concrexit/ontkoppelen")
def disconnect():
    same_origin()
    try:
        cfg, _ = load_cfg()
        logout(concrexit_settings(cfg), current_app.config["CONCREXIT_TOKENS"])
    except VerslagError as err:
        return render(str(err), 500)
    clear_events_cache()
    return redirect(url_for("concrexit.concrexit_page", **read_settings().query()))
