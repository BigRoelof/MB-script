"""Evenementlabels herkennen in documentnamen."""

import pytest

from verslag.events import reference_labels


@pytest.mark.parametrize("reference, labels", [
    ("Food Maandborrel September [9107] [E1202]", {"1202"}),
    ("2627017 E1206", {"1206"}),
    ("F2627001 E1205", {"1205"}),
    ("Sales at Shift 150 (542cdaa0-e123-4c87-a3c9-dbd06841f50a) [E1202]", {"1202"}),
    ("Membership renewal member (year) [542cdaa0-e123-4c87]", set()),
    ("2627020 e1206", set()),                      # kleine letter: geen label
    ("2627021 E1206 E1207", {"1206", "1207"}),     # twee evenementen (dan telt de referentie niet)
])
def test_labels_in_referentie(reference, labels):
    assert reference_labels(reference) == labels
