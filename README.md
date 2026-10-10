# Jaarverslag-tabellen uit Moneybird

`jaarverslag.py` haalt de cijfers van Thalia op uit Moneybird en schrijft LaTeX-tabellen voor het (half)jaarverslag:

| Bestand | Inhoud |
|---|---|
| `results_overview.tex` | Overzicht: Board, Borrels, Events, General, Incidental |
| `results_<categorie>.tex` | Eén tabel per categorie, één regel per code |
| `events_<organizer>.tex` | Per organizer (commissie/society) zijn evenementen met resultaat |
| `balance_sheet.tex` | Balans op begin- en einddatum |
| `partners.tex` | Partnercontracten: Invoice sent / Signed / Created |
| `example.tex` | Voorbeelddocument dat alles inlaadt (om te controleren) |

## Hoe de groepering werkt

Elk project in Moneybird heet `CODE - omschrijving`, bijvoorbeeld `C106 - iCIS borrel kwartaal 1`.

- Het **cijfer na de C** bepaalt de tabel (1 = Borrels, 2 = Events, 3 = General, 4 = Board, 5 = Incidental).
- De **hele code** bepaalt de regel (C106 = Other borrels).
- Alle projecten met dezelfde code worden opgeteld.

Welke code bij welke regelnaam hoort, staat in **`codes.csv`** (kolommen `code,name`, te bewerken in Excel of een teksteditor). Een nieuwe regel toevoegen is één regel in dat bestand. Het script weigert te draaien bij dubbele of ongeldige codes of een code waarvan de categorie niet in `config.toml` staat. De categorieën (cijfer, naam, bestandsnaam), het administratie-id en de opmaak staan in `config.toml`. Een ander codebestand kies je met `--codes`.

**Debit** = kosten, **credit** = opbrengsten, allebei exclusief btw, zoals Moneybird ze in de winst-en-verliesrekening boekt. **Result** = credit − debit.

## Evenementen

Een boekregel hoort bij een evenement via het label `[E<id>]` (event-id uit concrexit), in de omschrijving van de regel of in de naam van het document (zo pusht concrexit, bijv. `Food Maandborrel September [9107] [E1202]`). Naam, datum en organizer komen uit **`events.csv`** (export uit concrexit, kolommen `id,name,organizer,date`), standaard naast het script. Per organizer komt er een tabel met zijn evenementen. In `config.toml` staan onder `[events]` de codes waar elke regel een label hoort te hebben (`label_codes`) en optioneel nettere organizernamen (`[events.organizers]`).

## Gebruik

Python 3.11 of nieuwer, verder niets te installeren.

1. Maak een persoonlijk API-token aan via de ontwikkelaarsinstellingen van je Moneybird-account. Het script heeft leesrechten nodig op verkoopfacturen, documenten, offertes, bank en instellingen.
2. Zet het token in een omgevingsvariabele, niet in de code:
   ```sh
   export MONEYBIRD_TOKEN="..."
   ```
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
- `--budget begroting.csv`: begroting inlezen, kolommen `code,debit,credit` (komt momenteel niet in de tabellen).
- `--include-drafts`: conceptfacturen en -offertes meetellen in de partnertabellen.
- `--fixtures tests/fixtures`: draaien op opgeslagen testdata, zonder token.

## Controles

Na afloop print het script de totalen per categorie en een lijst **Controles** met per controle `OK`, `AFWIJKING` of `LET OP`:

- projectrapporten tegenover de winst-en-verliesrekening (opbrengsten en kosten);
- het totaal van de overzichtstabel tegenover de winst-en-verliesrekening (opbrengsten, kosten, resultaat);
- alle regels van de categorietabellen opgeteld tegenover het overzicht;
- balans: activa = passiva, en de regels tellen op tot het totaal (begin en eind);
- toename van het onafgesloten resultaat op de balans tegenover het resultaat in de tabel (alleen `LET OP`, omdat een afgesloten boekjaar dit verschil kan geven);
- alle boekregels onder de labelcodes tegenover de projectrapporten.

Bij een `AFWIJKING` eindigt het script met exitcode 1 (de tabellen worden wel geschreven). Daarnaast waarschuwt het bij:

- boekingen **zonder project**: die komen niet in de tabellen;
- projecten **zonder code** aan het begin van de naam;
- codes die niet in `codes.csv` staan;
- regels zonder `[E…]`-label onder de labelcodes, labels die niet in `events.csv` staan, en boekingen die ver van de evenementdatum liggen.

## Beperkingen

- Moneybird-rapporten werken met **hele maanden**, maximaal 12. Een peildatum als 28 januari kan niet; gebruik 31 januari.
- De resultaten komen uit de Moneybird-rapporten. **Conceptfacturen tellen daar niet mee.** Pas na versturen staan ze erin.
- De balans op de begindatum is de stand aan het eind van de maand ervoor (bij 1 september dus 31 augustus).
- Merchandisevoorraad en de schattingen bij partners (B.2) komen niet uit Moneybird en blijven handwerk.
