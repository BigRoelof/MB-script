"""Evenementen ophalen uit concrexit (de backend van thalia.nu) en events.csv daarmee bijwerken.

Gebruikt de admin-API (`/api/v2/admin/events/`), want alleen die geeft ook ongepubliceerde
evenementen. Daarvoor is een OAuth-token nodig met scope `events:admin`, van een account met
staff-status en het recht `events.view_event`. Het token staat als CONCREXIT_TOKEN in .env.

Concrexit is de bron: nieuwe en gewijzigde evenementen worden overgenomen. Een evenement dat in
events.csv staat maar niet (meer) in concrexit, blijft staan met een waarschuwing, want er kunnen
nog boekingen aan hangen.
"""

from __future__ import annotations

import datetime as dt
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .errors import VerslagError
from .events import Event, load_events, write_events
from .moneybird import get_json


DEFAULT_URL = "https://thalia.nu"
EVENTS_PATH = "api/v2/admin/events/"
PAGE_SIZE = 100
MESSAGES = {
    401: "Concrexit weigert het token (verlopen of ongeldig). Maak een nieuw token aan en zet het als "
         "CONCREXIT_TOKEN in .env.",
    403: "Concrexit geeft geen toegang tot de evenementen. Het token heeft scope events:admin nodig, en je "
         "account staff-status met het recht events.view_event.",
}


class ConcrexitClient:
    def __init__(self, token: str, base_url: str = DEFAULT_URL):
        self.token = token
        self.base = base_url.rstrip("/")

    def get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base}/{path}" + (f"?{query}" if query else "")
        return get_json(url, self.token, "Concrexit", path, MESSAGES)


def concrexit_url(cfg: dict) -> str:
    """CONCREXIT_URL uit de omgeving/.env gaat voor (handig voor een lokale concrexit), dan config.toml."""
    return os.environ.get("CONCREXIT_URL") or cfg.get("concrexit", {}).get("url", DEFAULT_URL)


def has_concrexit_token() -> bool:
    return bool(os.environ.get("CONCREXIT_TOKEN"))


def make_concrexit_client(cfg: dict) -> ConcrexitClient:
    token = os.environ.get("CONCREXIT_TOKEN")
    if not token:
        raise VerslagError("Geen concrexit-token gevonden. Zet CONCREXIT_TOKEN=... in het bestand .env naast "
                           "het script (een token met scope events:admin, zie .env.example).")
    return ConcrexitClient(token, concrexit_url(cfg))


def fetch_events(client, start: dt.date, end: dt.date) -> list[dict]:
    """Alle evenementen die (deels) in de periode vallen, ook ongepubliceerde."""
    params = {"start": f"{start.isoformat()}T00:00:00", "end": f"{end.isoformat()}T23:59:59",
              "ordering": "start", "limit": PAGE_SIZE}
    out, offset = [], 0
    while True:
        data = client.get(EVENTS_PATH, {**params, "offset": offset})
        results = data.get("results", [])
        out.extend(results)
        offset += len(results)
        if not results or offset >= data.get("count", 0):
            return out


@dataclass
class Fetched:
    events: dict[str, Event]
    unpublished: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def convert(raw: list[dict]) -> Fetched:
    """API-antwoorden → Event's. Organizer = de eerste organiserende groep (pk), met de naam erbij."""
    fetched = Fetched({})
    for r in raw:
        event_id = str(r["pk"])
        title = (r.get("title") or "").strip() or f"Event {event_id}"
        # concrexit geeft de tijd met de Nederlandse tijdzone erbij, dus de datum klopt zoals hij er staat
        date = dt.datetime.fromisoformat(r["start"]).date()
        organisers = r.get("organisers") or []
        if organisers:
            organizer, organizer_name = str(organisers[0]["pk"]), (organisers[0].get("name") or "").strip()
            if len(organisers) > 1:
                names = ", ".join(o.get("name") or str(o["pk"]) for o in organisers)
                fetched.warnings.append(f"E{event_id} '{title}' heeft {len(organisers)} organisatoren ({names}); "
                                        f"ingedeeld bij {organizer_name or organizer}.")
        else:
            organizer, organizer_name = "Other", ""
            fetched.warnings.append(f"E{event_id} '{title}' heeft geen organisator in concrexit; ingedeeld bij Other.")
        fetched.events[event_id] = Event(title, date, organizer, organizer_name)
        if not r.get("published", True):
            fetched.unpublished.add(event_id)
    return fetched


@dataclass
class SyncResult:
    merged: dict[str, Event]               # de nieuwe inhoud van events.csv
    added: list[str]
    changed: list[tuple[str, Event, Event]]  # (id, oud, nieuw)
    missing: list[str]                     # in events.csv en in de periode, maar niet in concrexit
    unchanged: int
    unpublished: set[str]
    warnings: list[str]
    written: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed)


def plan_sync(existing: dict[str, Event], fetched: Fetched, start: dt.date, end: dt.date) -> SyncResult:
    merged = dict(existing)
    added, changed, unchanged = [], [], 0
    for event_id, new in fetched.events.items():
        old = existing.get(event_id)
        if old is None:
            added.append(event_id)
        elif old != new:
            changed.append((event_id, old, new))
        else:
            unchanged += 1
        merged[event_id] = new
    missing = sorted((i for i, e in existing.items() if i not in fetched.events and start <= e.date <= end),
                     key=lambda i: (existing[i].date, int(i)))
    warnings = list(fetched.warnings)
    for i in missing:
        e = existing[i]
        warnings.append(f"E{i} '{e.name}' ({e.date:%d-%m-%Y}) staat in events.csv maar niet in concrexit. "
                        "Hij blijft staan, want er kunnen boekingen aan hangen; verwijder hem met de hand als hij weg mag.")
    return SyncResult(merged, sorted(added, key=int), changed, missing, unchanged, fetched.unpublished, warnings)


def sync_events(client, path: Path, start: dt.date, end: dt.date, write: bool = True) -> SyncResult:
    """Haalt de evenementen in de periode op en werkt events.csv bij (met write=False alleen kijken)."""
    existing = load_events(path) if path.exists() else {}
    result = plan_sync(existing, convert(fetch_events(client, start, end)), start, end)
    if write and result.has_changes:
        write_events(path, result.merged)
        result.written = True
    return result


def summary(result: SyncResult) -> str:
    total = len(result.added) + len(result.changed) + result.unchanged
    text = f"{total} evenementen in concrexit"
    if result.unpublished:
        text += f" (waarvan {len(result.unpublished)} ongepubliceerd)"
    text += f": {len(result.added)} nieuw, {len(result.changed)} gewijzigd, {result.unchanged} ongewijzigd"
    if result.missing:
        text += f"; {len(result.missing)} alleen in events.csv"
    return text
