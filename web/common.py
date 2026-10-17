"""Instellingen uit de URL en het laden van de gegevens (met een cache voor de Moneybird-data)."""

from __future__ import annotations

import calendar
import datetime as dt
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from flask import abort, current_app, jsonify, request

from verslag.budget import college_year, find_budget
from verslag.config import load_config, validate_period
from verslag.errors import VerslagError
from verslag.concrexit import load_event_list
from verslag.events import EventList, events_from_csv, find_events
from verslag.moneybird import fetch_data, make_client
from verslag.report import build_report

# Opgehaalde Moneybird-gegevens per (bron, administratie, periode): (tijdstip, data).
# De live API is traag en heeft een rate limit; "Opnieuw ophalen" leegt de cache.
_cache: dict[tuple, tuple[dt.datetime, dict]] = {}
# Evenementen van thalia.nu, met dezelfde sleutel en ook geleegd door "Opnieuw ophalen"
_events_cache: dict[tuple, EventList] = {}


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


def read_settings() -> Settings:
    year = college_year(dt.date.today())
    start = month_start(request.args.get("van"), dt.date(year, 9, 1))
    end = month_end(month_start(request.args.get("tm"), dt.date(year + 1, 8, 1)))
    source = request.args.get("bron")
    if source not in ("live", "testdata"):
        source = "live" if has_token() else "testdata"
    return Settings(start, end, source, request.args.get("begroting") != "0")


def load_cfg():
    """config.toml en codes.csv: (cfg, label_codes)."""
    return load_config(current_app.config["CONFIG"], current_app.config["CODES"])


def cache_key(s: Settings, cfg: dict) -> tuple:
    return (s.source, cfg.get("administration_id"), s.start, s.end)


def load_data(s: Settings, cfg: dict) -> tuple[dt.datetime, dict]:
    """De ruwe Moneybird-gegevens van deze bron en periode, uit de cache of opgehaald: (tijdstip, data)."""
    validate_period(s.start, s.end)
    key = cache_key(s, cfg)
    if key not in _cache:
        fixtures = current_app.config["FIXTURES"] if s.source == "testdata" else None
        _cache[key] = (dt.datetime.now(), fetch_data(make_client(cfg, fixtures), s.start, s.end))
    return _cache[key]


def load_events(s: Settings, cfg: dict) -> EventList:
    """De evenementenlijst. Bij de testdata altijd events.csv (elke keer opnieuw gelezen, zoals
    de begroting); live van thalia.nu als dat gekoppeld is (gecachet), anders events.csv."""
    if s.source == "testdata":
        return events_from_csv(find_events(None, current_app.config["FIXTURES"]))
    key = cache_key(s, cfg)
    if key not in _events_cache:
        event_list = load_event_list(cfg, s.start, s.end, store=current_app.config["CONCREXIT_TOKENS"])
        if event_list.source != "thalia.nu":
            return event_list   # events.csv: niet cachen, zodat een nieuwe export meteen zichtbaar is
        _events_cache[key] = event_list
    return _events_cache[key]


def load(s: Settings):
    """Geeft (cfg, report, opgehaald_om). Fouten komen als VerslagError."""
    validate_period(s.start, s.end)
    cfg, label_codes = load_cfg()
    fetched_at, data = load_data(s, cfg)
    # De begroting wordt elke keer opnieuw gelezen: wijzigingen in de editor zijn meteen zichtbaar
    report = build_report(cfg, label_codes, data, s.start, s.end,
                          load_events(s, cfg),
                          find_budget(s.start, current_app.config["BUDGET_DIR"]) if s.show_budget else None)
    return cfg, report, fetched_at


def load_or_error(s: Settings):
    try:
        return load(s), None
    except VerslagError as err:
        return None, str(err)


def clear_cache(s: Settings) -> None:
    """Vergeet de opgehaalde gegevens van deze bron en periode ("Opnieuw ophalen")."""
    for key in [k for k in _cache if k[0] == s.source and k[2:] == (s.start, s.end)]:
        del _cache[key]
    for key in [k for k in _events_cache if k[0] == s.source and k[2:] == (s.start, s.end)]:
        del _events_cache[key]


def clear_events_cache() -> None:
    """Na koppelen of ontkoppelen met thalia.nu: alle evenementenlijsten opnieuw ophalen."""
    _events_cache.clear()


# --------------------------------------------------------------------------
# Voor de API's (begroting en codes)
# --------------------------------------------------------------------------

def same_origin() -> None:
    """Weigert verzoeken die vanaf een andere website komen (browsers sturen dan hun Origin mee)."""
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        abort(403)


def json_body() -> dict:
    """Alleen JSON van deze pagina zelf. Een andere website kan zo geen wijzigingen insturen:
    JSON van een ander domein vraagt eerst toestemming (CORS) en die geven we niet."""
    if not request.is_json:
        abort(415)
    same_origin()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    return body


def api_error(message: str, status: int):
    return jsonify(error=message), status
