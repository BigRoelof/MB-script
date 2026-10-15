"""Het dashboard: pagina's, de schakelaar voor de begroting en de begrotings-API."""

import datetime as dt
import json
import re
import zipfile
from decimal import Decimal
from io import BytesIO

import pytest

import web.codes
from conftest import FIXTURES
from verslag.amounts import Amounts, Row
from verslag.budget import college_year
from web.filters import budget_progress, eur_filter

PERIOD = "van=2026-09&tm=2027-08&bron=testdata"


def version(client, year=2026):
    page = client.get(f"/begroting?jaar={year}&bron=testdata").get_data(as_text=True)
    return re.search(r'"version": "(\w*)"', page).group(1)


@pytest.mark.parametrize("url", ["/", "/?begroting=0", "/begroting?jaar=2026", "/begroting?jaar=2030",
                                 "/tex/results_overview.tex", "/zip"])
def test_paginas(client, url):
    sep = "&" if "?" in url else "?"
    assert client.get(f"{url}{sep}{PERIOD}").status_code == 200


def test_begroting_maken_opslaan_en_tonen(client):
    assert client.post("/api/begroting/2026", json={"bron": "leeg"}).status_code == 201
    assert client.post("/api/begroting/2026", json={"bron": "leeg"}).status_code == 409
    rows = {"C101": {"debit": "1500", "credit": "0", "note": "4 borrels"}}
    r = client.put("/api/begroting/2026", json={"version": version(client), "rows": rows})
    assert r.status_code == 200
    home = client.get(f"/?{PERIOD}").get_data(as_text=True)
    assert "with-budget" in home and "Begroting bewerken" in home
    home_uit = client.get(f"/?{PERIOD}&begroting=0").get_data(as_text=True)
    assert "with-budget" not in home_uit and "Begroting bewerken" not in home_uit
    names = zipfile.ZipFile(BytesIO(client.get(f"/zip?{PERIOD}").data)).namelist()
    assert "budget_overview.tex" in names
    names_uit = zipfile.ZipFile(BytesIO(client.get(f"/zip?{PERIOD}&begroting=0").data)).namelist()
    assert "budget_overview.tex" not in names_uit


def test_versieconflict_en_vergrendeling(client):
    client.post("/api/begroting/2026", json={"bron": "leeg"})
    old = version(client)
    changed = {"C101": {"debit": "100", "credit": "0", "note": ""}}
    assert client.put("/api/begroting/2026", json={"version": old, "rows": changed}).status_code == 200
    # Zelfde oude versie nog eens: het bestand is intussen veranderd, dus weigeren
    assert client.put("/api/begroting/2026", json={"version": old, "rows": {}}).status_code == 409
    v = version(client)
    r = client.post("/api/begroting/2026/status", json={"version": v, "status": "vastgesteld", "date": "2026-10-20"})
    assert r.status_code == 200
    assert client.put("/api/begroting/2026", json={"version": version(client), "rows": {}}).status_code == 423


def test_ongeldige_invoer(client):
    client.post("/api/begroting/2026", json={"bron": "leeg"})
    r = client.put("/api/begroting/2026", json={"version": version(client), "rows": {"C101": {"debit": "abc"}}})
    assert r.status_code == 400 and "geen bedrag" in r.json["error"]


def test_alleen_json_van_eigen_site(client):
    assert client.post("/api/begroting/2026", data={"bron": "leeg"}).status_code == 415
    assert client.post("/api/begroting/2026", json={"bron": "leeg"},
                       headers={"Origin": "http://andere-site.example"}).status_code == 403


def test_codes_via_api(client, codes_file):
    assert client.post("/api/codes", json={"code": "C107", "name": "Lustrum"}).status_code == 201
    assert client.post("/api/codes", json={"code": "C107", "name": "x"}).status_code == 400
    assert client.put("/api/codes/C107", json={"name": "Lustrumborrel"}).status_code == 200
    assert "C107,Lustrumborrel" in codes_file.read_text()


def row(budget, actual):
    # bedragen als resultaat: negatief = verlies
    def amounts(result):
        return Amounts(Decimal(-result), Decimal(0)) if result < 0 else Amounts(Decimal(0), Decimal(result))
    return Row("C101", "x", amounts(budget), amounts(actual))


@pytest.mark.parametrize("budget, actual, kind, over, flip", [
    (-1000, -500, "cost", False, None),
    (-1000, -1300, "cost", True, None),
    (1000, 1500, "income", False, None),
    (1000, -200, "income", True, "verlies"),
    (-1000, 200, "cost", False, "winst"),
    (0, -50, "unbudgeted", True, None),
])
def test_voortgangsbalk(budget, actual, kind, over, flip):
    p = budget_progress(row(budget, actual))
    assert (p["kind"], p["over"], p.get("flip")) == (kind, over, flip)


def test_voortgangsbalk_leeg_zonder_begroting_en_boekingen():
    assert budget_progress(row(0, 0)) is None


def test_euro_met_harde_spatie():
    assert eur_filter(Decimal("-1234.5")) == "−€ 1.234,50"


def test_codespagina(client):
    page = client.get(f"/codes?{PERIOD}").get_data(as_text=True)
    assert 'data-code="C101"' in page
    assert "C101 - Borrel Drink costs" in page          # project uit Moneybird
    assert "label verplicht" in page


def test_code_verwijderen_alleen_als_ongebruikt(client, codes_file):
    # C101: heeft een project in Moneybird en is een labelcode
    r = client.delete(f"/api/codes/C101?{PERIOD}", json={})
    assert r.status_code == 409 and "project" in r.json["error"]
    # C218 (WISO): geen project in de testdata, maar staat in de begroting
    client.post("/api/begroting/2026", json={"bron": "leeg"})
    client.put("/api/begroting/2026", json={"version": version(client),
                                            "rows": {"C218": {"debit": "250", "credit": "0"}}})
    r = client.delete(f"/api/codes/C218?{PERIOD}", json={})
    assert r.status_code == 409 and "begroting" in r.json["error"]
    # Een nieuwe, ongebruikte code mag weg
    client.post("/api/codes", json={"code": "C219", "name": "Nieuwe commissie"})
    assert client.delete(f"/api/codes/C219?{PERIOD}", json={}).status_code == 200
    assert "C219" not in codes_file.read_text()
    assert client.delete(f"/api/codes/C219?{PERIOD}", json={}).status_code == 400


def test_laatst_gebruikt_zoekt_terug_tot_een_leeg_jaar(client, monkeypatch):
    now = college_year(dt.date.today())
    projects = json.loads((FIXTURES / "projects.json").read_text())
    c101 = next(p["id"] for p in projects if p["name"].startswith("C101"))
    c105 = next(p["id"] for p in projects if p["name"].startswith("C105"))
    # C105 geboekt in de laatste twee jaar, C101 alleen twee jaar geleden; drie jaar geleden niets meer
    booked = {now: {c105}, now - 1: {c105}, now - 2: {c101}, now - 3: set()}
    asked = []

    def fake(source, cfg, year):
        asked.append(year)
        return booked.get(year, {c101})
    monkeypatch.setattr(web.codes, "booked_projects", fake)
    page = client.get(f"/codes?{PERIOD}").get_data(as_text=True)

    def usage(code):
        cell = re.search(rf'data-code="{code}".*?<td class="usage">(.*?)</td>', page, re.S).group(1)
        return re.sub(r"<[^>]+>", "", cell).strip()
    assert usage("C105") == "actief"
    assert usage("C101") == f"{now - 2}–{(now - 1) % 100:02d}"
    assert usage("C102") == "nog niet gebruikt"
    assert asked == [now, now - 1, now - 2, now - 3]   # stopt bij het lege jaar


def test_label_verplicht_via_api(client):
    assert client.put("/api/codes/C208/label", json={"required": True}).status_code == 200
    page = client.get(f"/codes?{PERIOD}").get_data(as_text=True)
    row = re.search(r'<tr data-code="C208">(.*?)</tr>', page, re.S).group(1)
    assert 'class="label-toggle" checked' in row
    assert client.put("/api/codes/C208/label", json={"required": False}).status_code == 200
    page = client.get(f"/codes?{PERIOD}").get_data(as_text=True)
    assert 'checked' not in re.search(r'<tr data-code="C208">(.*?)</tr>', page, re.S).group(1)
    assert client.put("/api/codes/C208/label", json={"required": "ja"}).status_code == 400
    assert client.put("/api/codes/C999/label", json={"required": True}).status_code == 400
