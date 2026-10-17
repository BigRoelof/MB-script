"""Rekenwerk achter jaarverslag.py en dashboard.py: van Moneybird-data naar de tabellen.

Modules, van onder naar boven (elke module gebruikt alleen modules die eerder in dit lijstje staan):

- amounts     bedragen, tabelregels en getalnotatie
- config      config.toml, codes.csv en de periode
- moneybird   ophalen uit de Moneybird-API (of uit opgeslagen testdata)
- budget      de begroting per collegejaar (begroting/<jaar>.csv)
- results     de tabellen per categorie en de controles daarop
- events      evenementlabels [E…], events.csv en de tabellen per organizer
- latex       de LaTeX-uitvoer
- report      voegt alles samen tot één Report
"""
