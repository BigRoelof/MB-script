"""codes.csv aanpassen vanuit de editor."""

import pytest

from verslag.config import edit_codes, load_codes

DIGITS = ["1", "2", "3", "4", "5"]


def test_toevoegen_op_de_juiste_plek_en_commentaar_blijft(codes_file):
    before = codes_file.read_text()
    edit_codes(codes_file, DIGITS, "c107", "Lustrum, borrel", new=True)
    lines = codes_file.read_text().splitlines()
    assert lines[lines.index("C106,Other borrels") + 1] == 'C107,"Lustrum, borrel"'
    assert [l for l in lines if l.startswith("#")] == [l for l in before.splitlines() if l.startswith("#")]
    assert load_codes(codes_file, DIGITS)["C107"] == "Lustrum, borrel"


def test_hernoemen(codes_file):
    edit_codes(codes_file, DIGITS, "C102", "  Borrel   bites ", new=False)
    assert load_codes(codes_file, DIGITS)["C102"] == "Borrel bites"


@pytest.mark.parametrize("code, name, new", [("C101", "x", True), ("C901", "x", True), ("C1x", "x", True),
                                             ("C108", "", True), ("C199", "x", False)])
def test_fouten(codes_file, code, name, new):
    before = codes_file.read_text()
    with pytest.raises(ValueError):
        edit_codes(codes_file, DIGITS, code, name, new=new)
    assert codes_file.read_text() == before


def test_verwijderen(codes_file):
    from verslag.config import delete_code
    delete_code(codes_file, DIGITS, "c216")
    assert "C216" not in load_codes(codes_file, DIGITS)
    assert codes_file.read_text().count("# Events") == 1   # commentaar blijft
    with pytest.raises(ValueError):
        delete_code(codes_file, DIGITS, "C216")


def test_label_verplicht_aan_en_uit(tmp_path, codes_file):
    import shutil
    import tomllib
    from verslag.config import PROJECT_DIR, set_label_code
    config = tmp_path / "config.toml"
    shutil.copy(PROJECT_DIR / "config.toml", config)
    before = config.read_text()
    set_label_code(config, codes_file, "c208", True)
    set_label_code(config, codes_file, "C104", False)
    after = config.read_text()
    codes = tomllib.loads(after)["events"]["label_codes"]
    assert "C208" in codes and "C104" not in codes and "C101" in codes
    assert '"C208",                   # End of semester party' in after
    # alles buiten de lijst blijft gelijk
    strip = lambda t: t[:t.index("label_codes = [")] + t[t.index("]", t.index("label_codes = [")):]
    assert strip(after) == strip(before)
    with pytest.raises(ValueError):
        set_label_code(config, codes_file, "C999", True)
