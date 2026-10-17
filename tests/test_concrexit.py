"""Evenementen van thalia.nu: ophalen, tokens, koppelen en terugvallen op events.csv.

Er gaat niets over het netwerk: get_json en post_form worden nagebootst, in de vorm van de
concrexit-code (events/api/v2/admin, django-oauth-toolkit).
"""

import base64
import datetime as dt
import hashlib
import urllib.parse

import pytest

from verslag import concrexit
from verslag.concrexit import (ConcrexitClient, ConcrexitSettings, Login, TokenStore, fetch_events,
                               finish_login, load_event_list, start_login)
from verslag.errors import VerslagError
from verslag.events import events_from_csv, find_events
from verslag.http_json import HTTPStatusError

from conftest import END, FIXTURES, START

SETTINGS = ConcrexitSettings("https://thalia.test", "abc123", "https://127.0.0.1/thalia-login")


def event(pk, title, start, *organisers):
    return {"pk": pk, "title": title, "start": start, "published": False,
            "organisers": [{"pk": i, "name": name} for i, name in enumerate(organisers)]}


class FakeClient:
    """Geeft de evenementen in pagina's van twee, zoals LimitOffsetPagination."""

    def __init__(self, events):
        self.events, self.calls = events, []

    def get(self, path, params):
        self.calls.append((path, params))
        offset = params["offset"]
        page = self.events[offset:offset + 2]
        more = offset + 2 < len(self.events)
        return {"count": len(self.events), "next": "volgende" if more else None, "results": page}


def test_evenementen_ophalen_met_paginering(config):
    client = FakeClient([
        event(1201, "Maandborrel september", "2026-09-10T17:00:00+02:00", "Borrel Committee"),
        event(1202, "Karaoke", "2026-10-15T23:30:00Z", "Activity Committee", "Borrel Committee"),
        event(1203, "Ongepubliceerd", "2026-11-01T12:00:00+01:00"),
    ])
    events = fetch_events(client, START, END)
    assert [path for path, _ in client.calls] == ["admin/events/", "admin/events/"]
    params = client.calls[0][1]
    # Concrexit leest start/end met parse_datetime: een kale datum wordt geweigerd
    assert params["start"] == "2025-08-31T00:00:00" and params["end"] == "2028-08-31T23:59:59"
    assert events["1201"].organizer == "Borrel Committee" and events["1201"].other_organizers == ()
    # 23:30 UTC is in oktober al 01:30 de volgende dag in Nijmegen
    assert events["1202"].date == dt.date(2026, 10, 16)
    assert events["1202"].organizer == "Activity Committee"
    assert events["1202"].other_organizers == ("Borrel Committee",)
    assert events["1203"].organizer == "Other"


def test_waarschuwing_bij_meer_organisers(config, data):
    from verslag.report import build_report
    cfg, label_codes = config
    event_list = events_from_csv(find_events(None, FIXTURES))
    first = next(iter(event_list.events))
    event_list.events[first].other_organizers = ("Lustrum Committee",)
    report = build_report(cfg, label_codes, data, START, END, event_list)
    assert any(f"E{first}" in w and "ook Lustrum Committee als organiser" in w for w in report.warnings)


def test_tokens_alleen_voor_jou_leesbaar(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save({"access_token": "a", "refresh_token": "r", "expires_in": 36000})
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.save({"access_token": "b", "expires_in": 10}, old_refresh_token="r")
    assert store.load()["refresh_token"] == "r"


@pytest.fixture
def server(monkeypatch):
    """Bootst de API en het token-endpoint na. `answers` zijn de antwoorden op GET's (of fouten)."""
    state = {"answers": [], "gets": [], "posts": [], "token": {"access_token": "nieuw", "refresh_token": "r2",
                                                                 "expires_in": 36000}}

    def get_json(url, headers, service, path):
        state["gets"].append((url, headers["Authorization"]))
        answer = state["answers"].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post_form(url, data, service, path):
        state["posts"].append((url, data))
        if isinstance(state["token"], Exception):
            raise state["token"]
        return state["token"]

    monkeypatch.setattr(concrexit, "get_json", get_json)
    monkeypatch.setattr(concrexit, "post_form", post_form)
    monkeypatch.delenv("CONCREXIT_TOKEN", raising=False)
    return state


def stored(tmp_path, expires_in=36000):
    store = TokenStore(tmp_path / "t.json")
    store.save({"access_token": "oud", "refresh_token": "r1", "expires_in": expires_in})
    return store


def test_verlopen_token_eerst_verversen(tmp_path, server):
    store = stored(tmp_path, expires_in=0)
    server["answers"] = [{"results": []}]
    assert ConcrexitClient(SETTINGS, store).get("admin/events/") == {"results": []}
    url, data = server["posts"][0]
    assert url == "https://thalia.test/user/oauth/token/"
    assert data == {"grant_type": "refresh_token", "refresh_token": "r1", "client_id": "abc123"}
    assert server["gets"][0][1] == "Bearer nieuw"
    assert store.load()["refresh_token"] == "r2"


def test_403_eenmaal_verversen_daarna_rechtenmelding(tmp_path, server):
    store = stored(tmp_path)
    server["answers"] = [HTTPStatusError("403", 403), {"ok": True}]
    assert ConcrexitClient(SETTINGS, store).get("admin/events/") == {"ok": True}
    assert [auth for _, auth in server["gets"]] == ["Bearer oud", "Bearer nieuw"]

    server["answers"] = [HTTPStatusError("403", 403), HTTPStatusError("403", 403)]
    with pytest.raises(VerslagError, match="staff"):
        ConcrexitClient(SETTINGS, store).get("admin/events/")


def test_ingetrokken_koppeling(tmp_path, server):
    server["token"] = HTTPStatusError("invalid_grant", 400)
    with pytest.raises(VerslagError, match="koppel opnieuw"):
        ConcrexitClient(SETTINGS, stored(tmp_path, expires_in=0)).get("admin/events/")


def test_koppelen_met_pkce(tmp_path, server):
    login = start_login(SETTINGS)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(login.url).query))
    assert login.url.startswith("https://thalia.test/user/oauth/authorize/?")
    assert query["scope"] == "events:admin" and query["redirect_uri"] == SETTINGS.redirect_uri
    challenge = base64.urlsafe_b64encode(hashlib.sha256(login.verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == challenge and query["code_challenge_method"] == "S256"

    store = TokenStore(tmp_path / "t.json")
    finish_login(SETTINGS, login, f"  {SETTINGS.redirect_uri}?code=xyz&state={login.state}\n", store)
    _, data = server["posts"][0]
    assert data["code"] == "xyz" and data["code_verifier"] == login.verifier
    assert data["grant_type"] == "authorization_code"
    assert store.load()["access_token"] == "nieuw"


@pytest.mark.parametrize("pasted, message", [
    ("https://127.0.0.1/thalia-login?error=access_denied&state=s", "geen toestemming"),
    ("https://127.0.0.1/thalia-login?state=s", "Geen code gevonden"),
    ("https://127.0.0.1/thalia-login?code=xyz&state=anders", "andere inlogpoging"),
])
def test_koppelen_mislukt(tmp_path, server, pasted, message):
    with pytest.raises(VerslagError, match=message):
        finish_login(SETTINGS, Login("", "s", "v"), pasted, TokenStore(tmp_path / "t.json"))
    assert server["posts"] == []


def test_zonder_client_id():
    with pytest.raises(VerslagError, match="client_id"):
        start_login(ConcrexitSettings("https://thalia.test", "", "https://127.0.0.1/thalia-login"))


def test_lijst_van_thalia_of_events_csv(config, tmp_path, server, monkeypatch):
    cfg, _ = config
    # Niet gekoppeld: events.csv
    assert load_event_list(cfg, START, END, store=TokenStore(tmp_path / "geen.json")).source.endswith("events.csv")
    # Gekoppeld: thalia.nu
    store = stored(tmp_path)
    server["answers"] = [{"next": None, "results": [event(1, "Borrel", "2026-09-10T17:00:00+02:00", "BC")]}]
    event_list = load_event_list(cfg, START, END, store=store)
    assert event_list.source == "thalia.nu" and list(event_list.events) == ["1"]
    # --events gaat voor, net als de testdata
    assert load_event_list(cfg, START, END, FIXTURES / "x.csv", store=store).source == ""
    # Thalia.nu niet bereikbaar: terug naar events.csv, met een waarschuwing
    server["answers"] = [VerslagError("thalia.nu is niet bereikbaar")]
    event_list = load_event_list(cfg, START, END, store=store)
    assert event_list.source.endswith("events.csv")
    assert event_list.warnings[0].startswith("Evenementen ophalen van thalia.nu mislukt: thalia.nu is niet bereikbaar")


# --------------------------------------------------------------------------
# Het dashboard
# --------------------------------------------------------------------------

def with_client_id(client):
    path = client.application.config["CONFIG"]
    path.write_text(path.read_text().replace('client_id = ""', 'client_id = "abc123"'))


def test_pagina_zonder_client_id(client):
    page = client.get("/concrexit?bron=testdata").get_data(as_text=True)
    assert "Nog niet in te stellen" in page and "Koppeling thalia.nu" in page


def test_koppelen_via_dashboard(client, server):
    with_client_id(client)
    assert "Inloggen op thalia.nu" in client.get("/concrexit?bron=testdata").get_data(as_text=True)
    r = client.post("/concrexit/start?bron=testdata")
    assert r.status_code == 302 and r.location.startswith("https://thalia.nu/user/oauth/authorize/")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.location).query))["state"]

    wrong = client.post("/concrexit/koppelen?bron=testdata", data={"adres": "https://127.0.0.1/thalia-login?code=x&state=y"})
    assert wrong.status_code == 400 and "andere inlogpoging" in wrong.get_data(as_text=True)

    r = client.post("/concrexit/koppelen?bron=testdata",
                    data={"adres": f"https://127.0.0.1/thalia-login?code=x&state={state}"})
    assert r.status_code == 302 and "gekoppeld=1" in r.location
    page = client.get(r.location).get_data(as_text=True)
    assert "Ontkoppelen" in page and "De evenementen komen voortaan van thalia.nu" in page

    r = client.post("/concrexit/ontkoppelen?bron=testdata")
    assert r.status_code == 302
    assert server["posts"][-1][0] == "https://thalia.nu/user/oauth/revoke_token/"
    assert not client.application.config["CONCREXIT_TOKENS"].path.exists()


def test_koppelen_niet_vanaf_andere_site(client):
    with_client_id(client)
    r = client.post("/concrexit/start", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
