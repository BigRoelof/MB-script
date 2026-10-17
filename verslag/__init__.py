"""Rekenwerk achter jaarverslag.py en dashboard.py: van Moneybird-data naar de tabellen.

Modules, van onder naar boven (elke module gebruikt alleen modules die eerder in dit lijstje staan):

- errors      VerslagError, de fout met een melding voor de penningmeester
- http_json   JSON ophalen over HTTP, met nieuwe pogingen bij storingen (Moneybird en concrexit)
- amounts     bedragen, tabelregels en getalnotatie
- config      config.toml, codes.csv en de periode
- moneybird   ophalen uit de Moneybird-API (of uit opgeslagen testdata)
- budget      de begroting per collegejaar (begroting/<jaar>.csv)
- results     de tabellen per categorie, de controles daarop en of alles verwerkt is
- events      evenementlabels [E…], events.csv en de tabellen per organizer
- concrexit   de evenementen van thalia.nu (OAuth-koppeling), met events.csv als terugval
- latex       de LaTeX-uitvoer
- report      voegt alles samen tot één Report
"""
