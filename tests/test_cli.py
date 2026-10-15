"""jaarverslag.py als commando."""

import jaarverslag

from conftest import FIXTURES


def test_script_schrijft_tabellen(tmp_path, capsys):
    out = tmp_path / "output"
    code = jaarverslag.main(["--fixtures", str(FIXTURES), "--start", "2026-09-01", "--end", "2027-08-31",
                             "--no-budget", "--out", str(out)])
    assert code == 0
    assert (out / "results_overview.tex").exists() and (out / "example.tex").exists()
    assert "10 van 10 in orde" in capsys.readouterr().err
