"""Het hele verslag op de testdata: controles, totalen en de LaTeX-bestanden."""

from decimal import Decimal

from verslag.amounts import Amounts
from verslag.budget import Budget
from verslag.events import find_events
from verslag.report import build_report

from conftest import END, FIXTURES, START


def report(config, data, budget=None, start=START, end=END):
    cfg, label_codes = config
    return build_report(cfg, label_codes, data, start, end, find_events(None, FIXTURES), budget)


def test_alle_controles_in_orde_en_geen_waarschuwingen(config, data):
    r = report(config, data)
    assert [c for c in r.checks if c[1] != "OK"] == []
    assert len(r.checks) == 10
    assert r.warnings == []


def test_totalen_kloppen_met_de_sandbox(config, data):
    grand = report(config, data).grand.actual
    assert grand.debit == Decimal("6822.18")
    assert grand.credit == Decimal("187.52")
    assert grand.result == Decimal("-6634.66")


def test_bestanden_zonder_begroting(config, data):
    files = report(config, data).files
    assert "results_overview.tex" in files and "example.tex" in files
    assert not any(name.startswith("budget_") for name in files)
    assert "\\textsc{budget}" not in files["results_borrels.tex"]
    assert any(name.startswith("events_") for name in files)


def test_bestanden_met_begroting(config, data):
    budget = Budget(2026, amounts={"C101": Amounts(Decimal(1000), Decimal(100))}, notes={"C101": "4 × 250"})
    r = report(config, data, budget)
    assert "\\textsc{budget}" in r.files["results_borrels.tex"]
    assert "\\textsc{difference}" in r.files["results_overview.tex"]
    assert "4 × 250" in r.files["budget_borrels.tex"]
    assert "budget_overview" in r.files["example.tex"]
    c101 = next(row for row in r.tables["1"] if row.code == "C101")
    assert c101.budget.result == Decimal(-900)
    assert c101.difference == c101.actual.result + 900


def test_waarschuwing_bij_conceptbegroting(config, data):
    concept = report(config, data, Budget(2026))
    assert any("concept" in w for w in concept.warnings)
    vastgesteld = report(config, data, Budget(2026, status="vastgesteld"))
    assert not any("concept" in w for w in vastgesteld.warnings)


def with_organizers(config, names):
    cfg, label_codes = config
    return {**cfg, "events": {**cfg["events"], "organizers": names}}, label_codes


def test_organizers_met_dezelfde_naam_worden_samengevoegd(config, data):
    names = {**config[0]["events"]["organizers"], "EVC": "BorrelCie"}
    r = report(with_organizers(config, names), data)
    headings = [o.heading for o in r.organizers]
    assert headings.count("BorrelCie") == 1
    assert "Events Committee" not in headings
    merged = next(o for o in r.organizers if o.heading == "BorrelCie")
    assert len(merged.rows) == 4 + 3          # vier van CAD, drie van EVC
    assert [row.date for row in merged.rows] == sorted(row.date for row in merged.rows)
    assert "events_eventscommittee.tex" not in r.files
    assert r.files["example.tex"].count("\\input{events_borrelcie}") == 1


def test_zelfde_bestandsnaam_krijgt_een_nummer(config, data):
    names = {**config[0]["events"]["organizers"], "EVC": "Borrel-Cie"}   # slug: borrelcie
    r = report(with_organizers(config, names), data)
    files = {o.heading: o.file for o in r.organizers}
    assert files["BorrelCie"] == "events_borrelcie.tex"
    assert files["Borrel-Cie"] == "events_borrelcie2.tex"
    assert "\\input{events_borrelcie2}" in r.files["example.tex"]
    assert any("events_borrelcie2.tex" in w for w in r.warnings)
