"""Evenementen rechtstreeks uit concrexit (thalia.nu), in plaats van een events.csv-export.

Concrexit kent geen persoonlijke API-tokens, alleen OAuth. Eenmalig koppelen gaat zo:
1. `start_login` geeft een adres op thalia.nu; daar log je in (met 2FA) en geef je toestemming.
2. Thalia.nu stuurt je door naar de redirect-URI (https://127.0.0.1/thalia-login?code=...). Daar draait
   niets, dus de browser toont een foutpagina; je plakt het adres uit de adresbalk terug.
3. `finish_login` ruilt de code in voor een access token (ca. 10 uur geldig) en een refresh token,
   waarmee de client daarna zelf nieuwe access tokens haalt. Ze staan in .concrexit-token.json
   (gitignored, alleen leesbaar voor jou).

Het admin-endpoint /api/v2/admin/events/ geeft ook ongepubliceerde evenementen. Het vraagt de scope
events:admin en een staff-account met het recht om events te bekijken (de penningmeester heeft dat).
Die scope geeft ook schrijfrechten; deze code doet alleen GET-verzoeken. Intrekken kan op thalia.nu
onder "Authorised applications".
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import PROJECT_DIR
from .errors import VerslagError
from .events import Event, EventList, events_from_csv, find_events
from .http_json import HTTPStatusError, get_json, post_form


SCOPE = "events:admin"
TOKEN_FILE = PROJECT_DIR / ".concrexit-token.json"
PAGE_SIZE = 100
# Er wordt ook geboekt op evenementen buiten de periode (een factuur die later binnenkomt);
# daarom halen we de evenementen van een jaar ervoor en een jaar erna ook op
MARGIN = dt.timedelta(days=366)
# Een access token zoveel seconden voor het verlopen al verversen
EXPIRY_MARGIN = 60
SERVICE = "thalia.nu"


@dataclass
class ConcrexitSettings:
    """[concrexit] uit config.toml."""
    base_url: str
    client_id: str
    redirect_uri: str


def concrexit_settings(cfg: dict) -> ConcrexitSettings:
    c = cfg.get("concrexit", {})
    return ConcrexitSettings(c.get("base_url", "https://thalia.nu").rstrip("/"), c.get("client_id", ""),
                             c.get("redirect_uri", "https://127.0.0.1/thalia-login"))


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

class TokenStore:
    """De tokens van de koppeling, in een bestand dat alleen jij kunt lezen."""

    def __init__(self, path: Path = TOKEN_FILE):
        self.path = path

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            raise VerslagError(f"{self.path.name} is beschadigd; koppel opnieuw met thalia.nu.")

    def save(self, response: dict, old_refresh_token: str = "") -> None:
        """Slaat een antwoord van het token-endpoint op (atomair, chmod 600)."""
        data = {
            "access_token": response["access_token"],
            # Concrexit geeft bij verversen een nieuw refresh token; het oude is dan niet meer geldig
            "refresh_token": response.get("refresh_token") or old_refresh_token,
            "expires_at": time.time() + int(response.get("expires_in", 3600)),
            "scope": response.get("scope", ""),
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def is_connected(store: TokenStore | None = None) -> bool:
    """Is er een koppeling: een token in de omgeving (CONCREXIT_TOKEN) of een opgeslagen token?"""
    return bool(os.environ.get("CONCREXIT_TOKEN")) or (store or TokenStore()).path.exists()


# --------------------------------------------------------------------------
# Koppelen (OAuth: authorization code met PKCE)
# --------------------------------------------------------------------------

@dataclass
class Login:
    """Een begonnen inlogpoging: het adres om te openen en wat nodig is om de code in te ruilen."""
    url: str
    state: str
    verifier: str


def _require_client_id(settings: ConcrexitSettings) -> None:
    if not settings.client_id:
        raise VerslagError("Vul in config.toml onder [concrexit] de client_id in. Die krijg je van de "
                           "TechniCie (OAuth-applicatie op thalia.nu).")


def start_login(settings: ConcrexitSettings) -> Login:
    _require_client_id(settings)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return Login(f"{settings.base_url}/user/oauth/authorize/?{query}", state, verifier)


def pasted_params(pasted: str) -> dict[str, str]:
    """De parameters uit het geplakte adres (https://127.0.0.1/thalia-login?code=...&state=...)."""
    query = urllib.parse.urlsplit(pasted.strip()).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}


def finish_login(settings: ConcrexitSettings, login: Login, pasted: str, store: TokenStore) -> None:
    """Ruilt de code uit het geplakte adres in voor tokens en slaat ze op."""
    _require_client_id(settings)
    params = pasted_params(pasted)
    if params.get("error") == "access_denied":
        raise VerslagError("Je hebt op thalia.nu geen toestemming gegeven; er is niets gekoppeld.")
    if "error" in params:
        raise VerslagError(f"Thalia.nu weigerde de koppeling: {params['error']}.")
    if "code" not in params:
        raise VerslagError("Geen code gevonden. Plak het hele adres uit de adresbalk, dat begint met "
                           f"{settings.redirect_uri}?code=")
    if params.get("state") != login.state:
        raise VerslagError("Dit adres hoort bij een andere inlogpoging. Begin opnieuw met koppelen.")
    try:
        response = post_form(f"{settings.base_url}/user/oauth/token/", {
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": settings.redirect_uri,
            "client_id": settings.client_id,
            "code_verifier": login.verifier,
        }, SERVICE, "token")
    except HTTPStatusError as err:
        raise VerslagError(f"De code is niet (meer) geldig; begin opnieuw met koppelen. ({err})")
    store.save(response)


def logout(settings: ConcrexitSettings, store: TokenStore) -> None:
    """Trekt het refresh token in op thalia.nu (als dat lukt) en verwijdert het tokenbestand."""
    tokens = store.load()
    if tokens and tokens.get("refresh_token") and settings.client_id:
        try:
            post_form(f"{settings.base_url}/user/oauth/revoke_token/", {
                "token": tokens["refresh_token"], "token_type_hint": "refresh_token",
                "client_id": settings.client_id}, SERVICE, "revoke_token")
        except VerslagError:
            pass   # dan staat het nog op thalia.nu onder "Authorised applications"
    store.delete()


# --------------------------------------------------------------------------
# De API
# --------------------------------------------------------------------------

class ConcrexitClient:
    """GET-verzoeken naar /api/v2/ op thalia.nu, met verversen van het token als het verlopen is."""

    def __init__(self, settings: ConcrexitSettings, store: TokenStore | None = None):
        self.settings = settings
        self.store = store or TokenStore()
        self.env_token = os.environ.get("CONCREXIT_TOKEN")   # handig om te testen, wordt niet ververst

    def get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.settings.base_url}/api/v2/{path}" + (f"?{query}" if query else "")
        try:
            return get_json(url, {"Authorization": f"Bearer {self._token()}"}, SERVICE, path)
        except HTTPStatusError as err:
            # Concrexit geeft 403 (geen 401) bij een verlopen of ingetrokken token: één keer verversen
            if err.status not in (401, 403):
                raise
            if not self.env_token:
                try:
                    return get_json(url, {"Authorization": f"Bearer {self._refresh()}"}, SERVICE, path)
                except HTTPStatusError as again:
                    if again.status not in (401, 403):
                        raise
            raise VerslagError(
                "Thalia.nu geeft geen toegang tot de evenementen. Je account moet staff zijn met het recht "
                "om events te bekijken, en de koppeling moet de scope events:admin hebben. "
                f"({err})")

    def _token(self) -> str:
        if self.env_token:
            return self.env_token
        tokens = self.store.load()
        if not tokens:
            raise VerslagError("Niet gekoppeld met thalia.nu.")
        if tokens.get("expires_at", 0) - EXPIRY_MARGIN < time.time():
            return self._refresh()
        return tokens["access_token"]

    def _refresh(self) -> str:
        tokens = self.store.load()
        if not tokens or not tokens.get("refresh_token"):
            raise VerslagError("Niet gekoppeld met thalia.nu.")
        try:
            response = post_form(f"{self.settings.base_url}/user/oauth/token/", {
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": self.settings.client_id,
            }, SERVICE, "token")
        except HTTPStatusError as err:
            raise VerslagError("De koppeling met thalia.nu is verlopen of ingetrokken; koppel opnieuw. "
                               f"({err})")
        self.store.save(response, tokens["refresh_token"])
        return response["access_token"]


def _local_date(text: str) -> dt.date:
    """De datum van een tijdstip uit concrexit, in Nederlandse tijd (een borrel tot na middernacht
    begint nog op de dag zelf)."""
    moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is not None:
        try:
            moment = moment.astimezone(ZoneInfo("Europe/Amsterdam"))
        except ZoneInfoNotFoundError:   # Windows zonder tzdata: dan de datum zoals concrexit hem geeft
            pass
    return moment.date()


def fetch_events(client, start: dt.date, end: dt.date) -> dict[str, Event]:
    """Alle evenementen (ook ongepubliceerde) rond de periode, op begindatum."""
    params = {
        "start": f"{start - MARGIN:%Y-%m-%d}T00:00:00",   # concrexit wil een tijdstip, geen kale datum
        "end": f"{end + MARGIN:%Y-%m-%d}T23:59:59",
        "ordering": "start",
        "limit": PAGE_SIZE,
    }
    events: dict[str, Event] = {}
    offset = 0
    while True:
        page = client.get("admin/events/", {**params, "offset": offset})
        results = page.get("results", [])
        for e in results:
            organizers = [o["name"] for o in e.get("organisers") or [] if o.get("name")]
            events[str(e["pk"])] = Event(e.get("title") or f"Event {e['pk']}", _local_date(e["start"]),
                                         organizers[0] if organizers else "Other", tuple(organizers[1:]))
        offset += len(results)
        if not page.get("next") or not results:
            return events


def load_event_list(cfg: dict, start: dt.date, end: dt.date, events_arg: Path | None = None,
                    fixtures: Path | None = None, store: TokenStore | None = None) -> EventList:
    """De evenementenlijst: van thalia.nu als dat gekoppeld is, anders events.csv.

    Een expliciet bestand (--events) of de testdata gaan altijd voor. Lukt ophalen van thalia.nu niet,
    dan events.csv met een waarschuwing, zodat de rest van het verslag gewoon werkt.
    """
    store = store or TokenStore()
    if events_arg is not None or fixtures is not None or not is_connected(store):
        return events_from_csv(find_events(events_arg, fixtures))
    try:
        events = fetch_events(ConcrexitClient(concrexit_settings(cfg), store), start, end)
    except VerslagError as err:
        fallback = events_from_csv(find_events(None, None))
        fallback.warnings.insert(0, f"Evenementen ophalen van thalia.nu mislukt: {err}"
                                    + (" Daarom events.csv gebruikt." if fallback.source else ""))
        return fallback
    return EventList(events, "thalia.nu")
