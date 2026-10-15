"""Instellingen uit de URL en het laden van de gegevens (met een cache voor de Moneybird-data)."""

from __future__ import annotations

import calendar
import datetime as dt
import os
import urllib.error
from dataclasses import dataclass

from flask import current_app, request

from verslag.budget import college_year, find_budget
from verslag.config import PROJECT_DIR, load_config, validate_period
from verslag.events import find_events
from verslag.moneybird import fetch_data, make_client
from verslag.report import build_report

# Opgehaalde Moneybird-gegevens per (bron, administratie, periode): (tijdstip, data).
# De live API is traag en heeft een rate limit; "Opnieuw ophalen" leegt de cache.
_cache: dict[tuple, tuple[dt.datetime, dict]] = {}


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
    return load_config(PROJECT_DIR / "config.toml", current_app.config["CODES"])


def cache_key(s: Settings, cfg: dict) -> tuple:
    return (s.source, cfg.get("administration_id"), s.start, s.end)


def load(s: Settings):
    """Geeft (cfg, report, opgehaald_om). Fouten komen als SystemExit (zoals in het script)."""
    validate_period(s.start, s.end)
    cfg, label_codes = load_cfg()
    fixtures = current_app.config["FIXTURES"] if s.source == "testdata" else None
    key = cache_key(s, cfg)
    if key not in _cache:
        client = make_client(cfg, fixtures)
        _cache[key] = (dt.datetime.now(), fetch_data(client, s.start, s.end))
    fetched_at, data = _cache[key]
    # De begroting wordt elke keer opnieuw gelezen: wijzigingen in de editor zijn meteen zichtbaar
    report = build_report(cfg, label_codes, data, s.start, s.end,
                          find_events(None, fixtures),
                          find_budget(s.start, current_app.config["BUDGET_DIR"]) if s.show_budget else None)
    return cfg, report, fetched_at


def load_or_error(s: Settings):
    try:
        return load(s), None
    except SystemExit as err:
        return None, str(err.code)
    except urllib.error.URLError as err:
        return None, f"Moneybird is niet bereikbaar ({err.reason}). Controleer je internetverbinding."


def clear_cache(s: Settings) -> None:
    """Vergeet de opgehaalde gegevens van deze bron en periode ("Opnieuw ophalen")."""
    for key in [k for k in _cache if k[0] == s.source and k[2:] == (s.start, s.end)]:
        del _cache[key]
