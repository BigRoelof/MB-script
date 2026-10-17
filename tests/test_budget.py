"""Begrotingsbestanden lezen en schrijven."""

import datetime as dt
from decimal import Decimal

import pytest

from verslag.amounts import Amounts
from verslag.budget import Budget, budget_path, budget_years, college_year, find_budget, load_budget, save_budget


def test_opslaan_en_teruglezen(tmp_path):
    original = Budget(2026, amounts={"C101": Amounts(Decimal("1500"), Decimal("250.50")),
                                     "C310": Amounts(Decimal(0), Decimal(15000))},
                      notes={"C101": "4 borrels, € 375 per borrel", "C102": "alleen toelichting"},
                      status="vastgesteld", adopted=dt.date(2026, 10, 20))
    path = budget_path(2026, tmp_path)
    save_budget(original, path)
    back = load_budget(path, 2026)
    assert back.amounts["C101"] == Amounts(Decimal("1500"), Decimal("250.50"))
    assert back.notes == original.notes
    assert back.locked and back.adopted == dt.date(2026, 10, 20)
    assert "C102" in back.amounts  # regel met alleen een toelichting blijft bestaan


def test_lege_regels_worden_niet_opgeslagen(tmp_path):
    path = tmp_path / "b.csv"
    save_budget(Budget(2026, amounts={"C101": Amounts()}), path)
    assert "C101" not in path.read_text()


def test_puntkomma_en_komma_uit_excel(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text("code;debit;credit\nC101;1500,25;0\n")
    assert load_budget(path).amounts["C101"].debit == Decimal("1500.25")


@pytest.mark.parametrize("content", ["code,debit,credit\nX1,1,1\n", "code,debit,credit\nC101,1,1\nC101,2,2\n",
                                     "code,debit,credit\nC101,abc,0\n"])
def test_ongeldige_bestanden(tmp_path, content):
    path = tmp_path / "b.csv"
    path.write_text(content)
    with pytest.raises(SystemExit):
        load_budget(path)


def test_jaren_en_collegejaar(tmp_path):
    for year in (2025, 2026):
        save_budget(Budget(year), budget_path(year, tmp_path))
    (tmp_path / "notities.csv").write_text("")
    assert budget_years(tmp_path) == [2025, 2026]
    assert college_year(dt.date(2027, 8, 31)) == 2026
    assert college_year(dt.date(2027, 9, 1)) == 2027
    assert find_budget(dt.date(2027, 1, 1), tmp_path).year == 2026
    assert find_budget(dt.date(2028, 1, 1), tmp_path) is None
