"""De Moneybird-client: opnieuw proberen bij rate limits, serverfouten en netwerkfouten."""

import io
import json
import urllib.error

import pytest

from verslag import moneybird
from verslag.errors import VerslagError
from verslag.moneybird import MoneybirdClient


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://moneybird.com", code, "fout", headers or {}, io.BytesIO(b'{"error":"x"}'))


@pytest.fixture
def answers(monkeypatch):
    """Zet de antwoorden klaar die urlopen achter elkaar geeft (een fout of een JSON-waarde),
    en houdt bij hoe lang er gewacht zou zijn."""
    queue, waits = [], []

    def urlopen(req, timeout):
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())

    monkeypatch.setattr(moneybird.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(moneybird.time, "sleep", waits.append)
    return queue, waits


def test_opnieuw_bij_serverfout_en_netwerkfout(answers):
    queue, waits = answers
    queue += [http_error(502), urllib.error.URLError("geen verbinding"), TimeoutError("time-out"), ["ok"]]
    assert MoneybirdClient("t", "1").get("projects") == ["ok"]
    assert waits == [2, 4, 8]


def test_geeft_op_na_drie_nieuwe_pogingen(answers):
    queue, waits = answers
    queue += [urllib.error.URLError("geen verbinding")] * 4
    with pytest.raises(VerslagError, match="niet bereikbaar \\(geen verbinding\\)"):
        MoneybirdClient("t", "1").get("projects")
    assert len(waits) == 3


def test_rate_limit_wacht_zo_lang_als_gevraagd(answers):
    queue, waits = answers
    queue += [http_error(429, {"Retry-After": "7"}), ["ok"]]
    assert MoneybirdClient("t", "1").get("projects") == ["ok"]
    assert waits == [7]


def test_andere_fout_meteen_melden(answers):
    queue, waits = answers
    queue += [http_error(404)]
    with pytest.raises(VerslagError, match="fout 404 op projects"):
        MoneybirdClient("t", "1").get("projects")
    assert waits == []
