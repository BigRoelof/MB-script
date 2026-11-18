"""Evenementen ophalen uit concrexit en events.csv bijwerken."""

import datetime as dt
import io
import json
import urllib.error

import pytest

import jaarverslag
from conftest import END, FIXTURES, START
from verslag import concrexit, moneybird
from verslag.concrexit import ConcrexitClient, convert, fetch_events, sync_events
from verslag.errors import VerslagError
from verslag.events import Event, load_events

RAW = json.loads((FIXTURES / "concrexit_events.json").read_text(encoding="utf-8"))

EXISTING = """# Evenementen van Thalia
id,name,organizer,date
1201,Introductieborrel eerstejaars,CAD,2026-09-03
1202,Maandborrel september,CAD,2026-09-17
1203,iCIS borrel kwartaal 1,CAD,2026-09-24
1100,Oud evenement,CAD,2026-05-01
"""


class FakeClient:
    """Geeft de evenementen uit de fixture terug zoals de admin-API dat doet: per pagina (limit/offset)."""

    def __init__(self, events=RAW):
        self.events, self.calls = events, []

    def get(self, path, params):
        self.calls.append(params)
        offset, limit = params["offset"], params["limit"]
        return {"count": len(self.events), "results": self.events[offset:offset + limit]}


@pytest.fixture
def events_file(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text(EXISTING, encoding="utf-8")
    return path


def test_ophalen_per_pagina(monkeypatch):
    monkeypatch.setattr(concrexit, "PAGE_SIZE", 2)
    client = FakeClient()
    assert [r["pk"] for r in fetch_events(client, START, END)] == [1201, 1202, 1204, 1214, 1215]
    assert [c["offset"] for c in client.calls] == [0, 2, 4]
    assert client.calls[0]["start"] == "2026-09-01T00:00:00" and client.calls[0]["end"] == "2027-08-31T23:59:59"


def test_omzetten():
    fetched = convert(RAW)
    # Laat op de avond blijft dezelfde dag (de tijd staat er in Nederlandse tijd)
    assert fetched.events["1204"] == Event("Turing diner", dt.date(2026, 9, 24), "31", "Turing Committee")
    assert fetched.events["1215"].organizer == "Other"
    assert fetched.unpublished == {"1214"}
    assert any("E1204" in w and "2 organisatoren" in w for w in fetched.warnings)
    assert any("E1215" in w and "geen organisator" in w for w in fetched.warnings)


def test_bijwerken(events_file):
    result = sync_events(FakeClient(), events_file, START, END)
    assert result.written
    assert result.added == ["1204", "1214", "1215"]
    assert [i for i, _, _ in result.changed] == ["1201", "1202"]   # organizer CAD → pk 12, naam
    assert result.missing == ["1203"]                              # alleen in events.csv, in de periode
    assert any("E1203" in w and "blijft staan" in w for w in result.warnings)

    events = load_events(events_file)
    assert events["1203"].name == "iCIS borrel kwartaal 1"    # niet weggehaald
    assert events["1100"].name == "Oud evenement"             # buiten de periode: blijft, zonder waarschuwing
    assert events["1202"] == Event("Maandborrel September", dt.date(2026, 9, 17), "12", "Borrel Committee")
    text = events_file.read_text(encoding="utf-8")
    assert text.startswith("# Evenementen van Thalia\nid,name,organizer,organizer_name,date\n1100,")

    # Nog een keer: niets meer te doen
    again = sync_events(FakeClient(), events_file, START, END)
    assert not again.has_changes and not again.written and again.unchanged == 5


def test_alleen_kijken_schrijft_niets(events_file):
    result = sync_events(FakeClient(), events_file, START, END, write=False)
    assert result.has_changes and not result.written
    assert events_file.read_text(encoding="utf-8") == EXISTING


def test_zonder_events_csv(tmp_path):
    path = tmp_path / "events.csv"
    assert sync_events(FakeClient(), path, START, END).written
    assert len(load_events(path)) == 5


def test_organizernaam(events_file):
    sync_events(FakeClient(), events_file, START, END)
    events = load_events(events_file)
    assert events["1204"].heading({"31": "Turing Committee"}) == "Turing Committee"
    assert events["1202"].heading({}) == "Borrel Committee"   # naam uit concrexit
    assert events["1100"].heading({"CAD": "BorrelCie"}) == "BorrelCie"


def test_token_geweigerd(monkeypatch):
    def urlopen(req, timeout):
        assert req.get_header("Authorization") == "Bearer t"
        raise urllib.error.HTTPError(req.full_url, 403, "fout", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(moneybird.urllib.request, "urlopen", urlopen)
    with pytest.raises(VerslagError, match="scope events:admin"):
        ConcrexitClient("t").get(concrexit.EVENTS_PATH, {"limit": 1})


def test_geen_token(monkeypatch):
    monkeypatch.delenv("CONCREXIT_TOKEN", raising=False)
    with pytest.raises(VerslagError, match="CONCREXIT_TOKEN"):
        concrexit.make_concrexit_client({})


def test_commando(monkeypatch, events_file, tmp_path, capsys):
    monkeypatch.setattr(jaarverslag, "make_concrexit_client", lambda cfg: FakeClient())
    code = jaarverslag.main(["--fixtures", str(FIXTURES), "--events", str(events_file), "--no-budget",
                             "--start", "2026-09-01", "--end", "2027-08-31", "--out", str(tmp_path / "out"),
                             "--sync-events"])
    err = capsys.readouterr().err
    assert "5 evenementen in concrexit (waarvan 1 ongepubliceerd): 3 nieuw, 2 gewijzigd" in err
    assert "E1203" in err
    assert code in (0, 1) and "1214" in events_file.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

PERIOD = "van=2026-09&tm=2027-08&bron=testdata"


@pytest.fixture
def web(client, events_file, monkeypatch):
    client.application.config["EVENTS"] = events_file
    monkeypatch.setenv("CONCREXIT_TOKEN", "t")
    monkeypatch.setattr("web.events.make_concrexit_client", lambda cfg: FakeClient())
    return client


def test_pagina_zonder_token(client, monkeypatch):
    monkeypatch.delenv("CONCREXIT_TOKEN", raising=False)
    page = client.get(f"/evenementen?{PERIOD}").get_data(as_text=True)
    assert "Nog geen concrexit-token" in page


def test_pagina_laat_eerst_zien_en_schrijft_na_klik(web, events_file):
    page = web.get(f"/evenementen?{PERIOD}").get_data(as_text=True)
    assert "Bestuurswissel-borrel" in page and "ongepubliceerd" in page and "events.csv bijwerken" in page
    assert events_file.read_text(encoding="utf-8") == EXISTING
    page = web.post(f"/evenementen?{PERIOD}").get_data(as_text=True)
    assert "is bijgewerkt" in page
    assert "1214" in events_file.read_text(encoding="utf-8")
    assert "is al bij" in web.get(f"/evenementen?{PERIOD}").get_data(as_text=True)


def test_niet_vanaf_andere_site(web, events_file):
    r = web.post(f"/evenementen?{PERIOD}", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert events_file.read_text(encoding="utf-8") == EXISTING
