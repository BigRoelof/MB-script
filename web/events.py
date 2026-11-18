"""Evenementen uit concrexit (/evenementen): eerst laten zien wat er verandert, dan events.csv bijwerken."""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, render_template, request

from verslag.concrexit import concrexit_url, has_concrexit_token, make_concrexit_client, summary, sync_events
from verslag.errors import VerslagError

from .common import has_token, load_cfg, read_settings

bp = Blueprint("events", __name__)


def render(write: bool):
    s = read_settings()
    path = current_app.config["EVENTS"]
    context = {"s": s, "has_token": has_token(), "has_concrexit_token": has_concrexit_token(),
               "events_file": path.name, "result": None, "error": None}
    try:
        cfg, _ = load_cfg()
        context["concrexit_url"] = concrexit_url(cfg)
        context["names"] = cfg["events"].get("organizers", {})
        if has_concrexit_token():
            result = sync_events(make_concrexit_client(cfg), path, s.start, s.end, write=write)
            context.update(result=result, summary=summary(result))
    except VerslagError as err:
        context["error"] = str(err)
    return render_template("evenementen.html", **context), (500 if context["error"] else 200)


@bp.get("/evenementen")
def events_page():
    return render(write=False)


@bp.post("/evenementen")
def events_sync():
    # Alleen vanaf deze site: een andere website mag events.csv niet laten bijwerken
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        abort(403)
    return render(write=True)
