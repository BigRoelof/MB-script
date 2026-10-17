# Jaarverslag-tabellen uit Moneybird

`jaarverslag.py` haalt de cijfers van Thalia op uit Moneybird en schrijft LaTeX-tabellen voor het (half)jaarverslag:

| Bestand | Inhoud |
|---|---|
| `results_overview.tex` | Overzicht: Board, Borrels, Events, General, Incidental |
| `results_<categorie>.tex` | Eén tabel per categorie, één regel per code (met begroting erbij: kolommen budget en difference) |
| `budget_overview.tex`, `budget_<categorie>.tex` | De begroting zelf, met toelichting (alleen als er een begroting is) |
| `events_<organizer>.tex` | Per organizer (commissie/society) zijn evenementen met resultaat |
| `example.tex` | Voorbeelddocument dat alles inlaadt (om te controleren) |

## Dashboard

`dashboard.py` toont alles op één pagina in de browser: tabellen per categorie (met de schakelaar **Begroting weergeven**: begroot, realisatie, verschil en een voortgangsbalk per regel), evenementen per organizer (met de boekregels erachter), controles en waarschuwingen. De categorietabellen hebben een downloadknop voor de `.tex`, en met **Download alle tabellen** krijg je een zip met precies dezelfde bestanden als het script schrijft (met de begroting uit: zonder begroting).

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # eenmalig
cp .env.example .env && chmod 600 .env   # token invullen; alleen nodig voor live gegevens
.venv/bin/python dashboard.py     # open http://127.0.0.1:5000
```

Periode en bron kies je in het **menu linksboven**; daar staan ook **Opnieuw ophalen** en de links naar Begroting en Codes. Zonder token kies je **Testdata** als bron (`tests/fixtures`, alleen collegejaar 2026-2027). Opgehaalde gegevens blijven in het geheugen tot je op **Opnieuw ophalen** klikt. Wijzigingen in `config.toml`, `codes.csv` en `events.csv` zie je na herladen van de pagina. Het dashboard is alleen bereikbaar vanaf je eigen computer.

## Hoe de groepering werkt

Elk project in Moneybird heet `CODE - omschrijving`, bijvoorbeeld `C106 - iCIS borrel kwartaal 1`.

- Het **cijfer na de C** bepaalt de tabel (1 = Borrels, 2 = Events, 3 = General, 4 = Board, 5 = Incidental).
- De **hele code** bepaalt de regel (C106 = Other borrels).
- Alle projecten met dezelfde code worden opgeteld.

Welke code bij welke regelnaam hoort, staat in **`codes.csv`** (kolommen `code,name`). Bewerken kan op de pagina **Codes** in het dashboard (`/codes`, via het menu linksboven): daar zie je per code de projecten in Moneybird en of er in de gekozen periode op geboekt is ("actief"), en kun je codes toevoegen, hernoemen en verwijderen. In de kolom **Label verplicht** vink je aan bij welke codes elke boekregel een evenementlabel `[E…]` moet hebben (dat past `label_codes` in `config.toml` aan). Verwijderen kan alleen als de code nergens gebruikt wordt (geen project in Moneybird, niet begroot, geen labelcode). Met de hand in Excel of een teksteditor mag ook. Het script weigert te draaien bij dubbele of ongeldige codes of een code waarvan de categorie niet in `config.toml` staat. De categorieën (cijfer, naam, bestandsnaam), het administratie-id en de opmaak staan in `config.toml`. Een ander codebestand kies je met `--codes`.

**Debit** = kosten, **credit** = opbrengsten, allebei exclusief btw, zoals Moneybird ze in de winst-en-verliesrekening boekt. **Result** = credit − debit.

## Evenementen

Een boekregel hoort bij een evenement via het label `[E<id>]` (event-id uit concrexit), in de omschrijving van de regel of in de naam van het document. In de documentnaam mag het tussen haken (zo pusht concrexit, bijv. `Food Maandborrel September [9107] [E1202]`) of los erachter, zoals je het zelf typt: `2627017 E1206` of `F2627001 E1205` (met hoofdletter E). Noemt een documentnaam meer dan één evenement, dan zet je het label per regel in de omschrijving. Naam, datum en organizer komen uit **`events.csv`** (export uit concrexit, kolommen `id,name,organizer,date`), standaard naast het script. Per organizer komt er een tabel met zijn evenementen. In `config.toml` staan onder `[events]` de codes waar elke regel een label hoort te hebben (`label_codes`) en optioneel nettere organizernamen (`[events.organizers]`).

## Begroting

Op `/begroting` in het dashboard maak je per collegejaar de begroting, per C-code kosten en opbrengsten (excl. btw) met een toelichting. Alle totalen rekenen mee terwijl je typt en elke wijziging wordt meteen opgeslagen in `begroting/<jaar>.csv` (bijv. `begroting/2026-2027.csv`; zet dat bestand in git). Handig:

- In een bedragcel mag je rekenen (`4*375`, `12*(15+3,50)`); `1.500` en `1.234,56` worden begrepen. <kbd>Enter</kbd> of de pijltjes gaan naar de volgende of vorige regel.
- Een nieuw jaar begin je leeg, als kopie van de begroting van vorig jaar, of met de realisatie van vorig jaar (afgerond op hele euro's).
- Codes toevoegen of hernoemen kan vanuit de editor; dat past `codes.csv` aan (commentaar en volgorde blijven staan). Verwijderen kan op de pagina Codes.
- **Vaststellen** (met de datum van de ALV) vergrendelt de begroting; **Ontgrendelen** maakt er weer een concept van. Een conceptbegroting geeft een waarschuwing in het verslag.

Op de startpagina staat de realisatie naast de begroting van het collegejaar waarin de gekozen periode begint. Bij een halfjaar is dat de realisatie tot nu toe tegenover de **hele** jaarbegroting.

## Gebruik

Python 3.11 of nieuwer, verder niets te installeren.

1. Maak een persoonlijk API-token aan via de ontwikkelaarsinstellingen van je Moneybird-account. Het script heeft leesrechten nodig op verkoopfacturen, documenten en instellingen.
2. Zet het token in het bestand `.env` naast het script (staat in `.gitignore`, komt dus nooit in git):
   ```sh
   cp .env.example .env && chmod 600 .env
   # open .env en vul in: MONEYBIRD_TOKEN=...
   ```
   Een omgevingsvariabele (`export MONEYBIRD_TOKEN=...`) mag ook en gaat voor op `.env`. Na het aanpassen van `.env` het dashboard herstarten.
3. Draai het script:
   ```sh
   # heel collegejaar (standaard)
   python3 jaarverslag.py
   # halfjaarverslag
   python3 jaarverslag.py --start 2026-09-01 --end 2027-01-31
   ```
4. Neem de tabellen op in je verslag (vereist `booktabs` en `tabularx`):
   ```latex
   {\small\input{output/results_borrels}}
   ```

Opties:

- `--codes BESTAND`: ander codebestand (standaard `codes.csv`).
- `--out MAP`: waar de bestanden komen (standaard `output`).
- `--events BESTAND`: andere evenementenlijst (standaard `events.csv` naast het script, of in de `--fixtures`-map).
- `--budget BESTAND`: andere begroting (standaard `begroting/<collegejaar>.csv`, het jaar waarin `--start` valt).
- `--no-budget`: zonder begroting (tabellen zonder budgetkolommen).
- `--fixtures tests/fixtures`: draaien op opgeslagen testdata, zonder token.

## Controles

Na afloop print het script de totalen per categorie en een lijst **Controles** met per controle `OK`, `AFWIJKING` of `LET OP`:

- projectrapporten tegenover de winst-en-verliesrekening (opbrengsten en kosten);
- het totaal van de overzichtstabel tegenover de winst-en-verliesrekening (opbrengsten, kosten, resultaat);
- alle regels van de categorietabellen opgeteld tegenover het overzicht;
- alle boekregels onder de labelcodes tegenover de projectrapporten.

Bij een `AFWIJKING` eindigt het script met exitcode 1 (de tabellen worden wel geschreven). Daarnaast waarschuwt het bij:

- boekingen **zonder project**: die komen niet in de tabellen;
- projecten **zonder code** aan het begin van de naam;
- codes die niet in `codes.csv` staan;
- regels zonder `[E…]`-label onder de labelcodes, labels die niet in `events.csv` staan, en boekingen die ver van de evenementdatum liggen.

## Code en tests

- `jaarverslag.py` is het commando; het rekenwerk staat in het pakket **`verslag/`** (één module per onderwerp: `moneybird`, `config`, `budget`, `results`, `events`, `latex`, `report`; zie de docstring in `verslag/__init__.py`).
- `dashboard.py` start de webapp; de code daarvan staat in **`web/`** (startpagina en downloads in `pages.py`, de begroting in `budget.py`). Templates en stijlen staan in `templates/` en `static/`.
- Tests (eenmalig `.venv/bin/pip install -r requirements-dev.txt`):
  ```sh
  .venv/bin/python -m pytest
  ```
  Ze draaien op de testdata en schrijven alleen in tijdelijke mappen (niet in `begroting/` of `codes.csv`).

## Beperkingen

- Moneybird-rapporten werken met **hele maanden**, maximaal 12. Een peildatum als 28 januari kan niet; gebruik 31 januari.
- De resultaten komen uit de Moneybird-rapporten. **Conceptfacturen tellen daar niet mee.** Pas na versturen staan ze erin.
- Merchandisevoorraad en de schattingen bij partners (B.2) komen niet uit Moneybird en blijven handwerk.
