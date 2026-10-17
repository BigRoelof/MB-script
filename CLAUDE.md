# Context voor Claude Code: Thalia jaarverslag-tabellen

Communiceer met de gebruiker in het **Nederlands**. De gebruiker is penningmeester van studievereniging Thalia (Computing Science, Radboud Universiteit Nijmegen).

## Doel

Thalia boekhoudt in Moneybird. `jaarverslag.py` genereert de financiële tabellen voor het (half)jaarverslag als **LaTeX** uit Moneybird:

1. **Zes basistabellen**: overzicht (`results_overview.tex`) + per categorie Borrels, Events, General, Board, Incidental (`results_<file>.tex`). Kolommen **debit, credit, result**; als er een begroting is ervoor **budget** (begroot resultaat) en erna **difference** (result − budget).
2. **Per organizer een tabel met zijn evenementen** (`events_<organizer>.tex`), kolommen event, date, debit, credit, result.
3. **Begroting** (als die er is): `budget_overview.tex` + `budget_<file>.tex` (debit, credit, result, en explanation als er toelichtingen zijn), voor de ALV.
4. Daarnaast `example.tex` dat alles inlaadt. (Balans en partnertabellen zijn op 15 okt 2026 op verzoek weggehaald.)

Debit = kosten, credit = opbrengsten, excl. btw. Result = credit − debit. Merchandisevoorraad en partnerschattingen (B.2) blijven handwerk.

## Hoe Thalia boekt

- Alles komt onder een **project** `C### - omschrijving`. Eerste cijfer = categorie/tabel (1 Borrels, 2 Events, 3 General, 4 Board, 5 Incidental), hele code = regel; projecten met dezelfde code worden opgeteld. Codes en regelnamen staan in `codes.csv`.
- **Eén project per code** (bijv. `C101 - Borrel Drink costs`), niet meer één project per evenement. Het evenement staat als label **`[E<id>]`**, met het event-id uit concrexit (de Django-backend van thalia.nu). Elk evenement staat in concrexit (eventueel ongepubliceerd).
- Thalia werkt niet met uren. Boekjaar = collegejaar, 1 september t/m 31 augustus.
- **Namen van documenten** (zoals in de echte administratie):
  - Inkomend (inkoopfacturen, bonnen, declaraties van leden): volgnummer per collegejaar, `2627001` = eerste van 2026-2027, met het event erachter als het om een evenement gaat: `2627017 E1206`.
  - Verkoopfacturen: `F2627001`, met event `F2627001 E1205` (referentieveld; het echte factuurnummer kent Moneybird toe bij versturen).
  - Memorialen zonder factuur: gewone naam, eventueel met label (`Kasverschil Borrel [E1208]`).
- **Concrexit-pushes** (externe verkoopfacturen, bron `Concrexit (thalia.nu)`), één push per inschrijving/bestelling. Het label staat in de **referentie**, met een eigen id ervoor zodat de referentie uniek is (Moneybird eist unieke referenties per externe factuur, anders 422):
  - Inschrijving: `Karaoke with EVC [43494] [E1234]`, regel `Event registration Karaoke with EVC. Oct. 15, 2026. Registration date: Oct. 8, 2026.`, 0% btw.
  - Drankjes: `Sales at Shift 150 - Maandborrel September (<order-uuid>) [E1202]`, contact vaak `Unknown Payer`, regel `1x Desperados. Ordered at 16:32:29.523903`, 21% btw incl.
  - Eten (project C314): `Food Maandborrel September [9107] [E1202]`, regel `Food order by Jane Smith for Maandborrel September`, 0% btw. 
  - Lidmaatschap (geen event, project C311): `Membership renewal member (year) [<uuid>]` (ook `Membership registration member (year|study)`), regel `Membership renewal member (year). Creation date: … Completion date: …` + `Date: …`, 0% btw.
  - Het eventlabel houdt de `E`; losse getallen/uuid's tussen haken zijn concrexit-id's en worden genegeerd.
- **Nog te bouwen in concrexit** (door de gebruiker): pushen naar vaste kostenplaatsen met bovenstaand formaat, en een exportknop die `events.csv` levert.

## Code

- **Opbouw** (opgesplitst op 15 okt 2026; uitvoer byte-voor-byte gelijk gecontroleerd):
  - `jaarverslag.py`: alleen het commando (`parse_args`, `main`).
  - `verslag/` (rekenwerk, alleen standaardbibliotheek): `amounts` (Amounts, Row, total, fmt_plain/NUMBER_FMT, CODE_RE) → `config` (PROJECT_DIR, load_config, load_codes, edit_codes, validate_period) → `moneybird` (clients, fetch_data, load_dotenv) → `budget` (Budget, college_year, load/save/find_budget) → `results` (build_results, run_checks) → `events` (labels, events.csv, organizer_groups, controles/waarschuwingen) → `latex` (tabellen, EXAMPLE_DOC) → `report` (Report, build_report). Imports lopen alleen naar beneden in dit rijtje. Standaardpaden via `config.PROJECT_DIR`.
  - `dashboard.py`: alleen `app = create_app()` + `main()`. `web/`: `__init__` (create_app, root_path = projectmap voor templates/static), `common` (Settings, laden + cache), `filters` (num/eur met harde spatie, datum, `progress`), `pages` (blueprint `pages`: index, refresh, tex, zip), `budget` (blueprint `begroting`: editor + API), `codes` (blueprint `codes`: pagina `/codes` met per code de Moneybird-projecten (uit `data["projects"]`, via `common.load_data`), kolom **Label verplicht** met vinkje per code (`PUT /api/codes/<code>/label`, `verslag/config.set_label_code`: herschrijft alleen de lijst `label_codes` in config.toml, één code per regel met de naam als commentaar; `app.config["CONFIG"]`), en **Geboekt in periode**: "actief" als de code in de gekozen periode kosten of opbrengsten heeft, anders "geen boekingen" (uit de projectrapporten in `data`, dus geen extra requests; op verzoek 17 okt, eerder zocht dit tot 10 jaar terug); `/api/codes` POST/PUT/DELETE; verwijderen alleen als ongebruikt, server controleert opnieuw, en niet als Moneybird niet te laden is; `verslag/config.delete_code`). `json_body`/`api_error` staan in `common`. In templates: `url_for('pages.index')`, `url_for('begroting.budget_page')` enz.
  - Tests: `.venv/bin/python -m pytest` (55 tests; `requirements-dev.txt`, `pytest.ini`): rapport op fixtures, begrotingsbestanden, codes.csv bewerken, labels, CLI, web/API (met tijdelijke begrotingsmap en kopie van codes.csv) en de JS-bedragparser via Node (overgeslagen zonder Node).
- `jaarverslag.py` (+ `verslag/`): alleen standaardbibliotheek, Python 3.11+. Leest `MONEYBIRD_TOKEN` uit de omgeving of uit `.env` naast het script (`load_dotenv`, eigen parser; omgeving gaat voor; waarschuwt als `.env` niet `chmod 600` is; `.env.example` is het sjabloon). Opgedeeld in `load_config` → `make_client` → `fetch_data` → `build_report` (geeft een `Report` met gestructureerde data + `files`: bestandsnaam → LaTeX); `main()` schrijft alleen de bestanden en de samenvatting.
- `dashboard.py` (Flask, in `.venv`, `requirements.txt`): lokale webapp op 127.0.0.1:5000 bovenop `build_report`. Startpagina (`templates/index.html`) en begrotingseditor (`templates/begroting.html`), met `static/dashboard.css`, `static/dashboard.js`, `static/begroting.js`, zelfgehost lettertype Archivo in `static/fonts/`. Bovenbalk: uitklapmenu linksboven (`templates/_menu.html`, op alle pagina's: periode en bron, opnieuw ophalen met terugkeer via `next`, links Dashboard/Begroting/Codes), wordmark, en rechts alleen "Download alle tabellen". Onder de bovenbalk alleen de gekozen periode als tekst (geen snelkeuzeknoppen meer, op verzoek 17 okt). Periode, bron en begroting in de URL (`van=2026-09&tm=2027-08&bron=testdata|live&begroting=0`). Downloads: `/tex/<bestand>`, `/zip` (identiek aan de scriptuitvoer). De CSV-export van boekregels is op verzoek weggehaald. Moneybird-data wordt per bron/administratie/periode in het geheugen gecachet; "Opnieuw ophalen" leegt die. Ontwerp: Thalia-magenta alleen voor bediening; datakleuren (opbrengsten blauw `#2a78d6`, kosten oranje `#eb6834`, verlies rood) zijn met de dataviz-validator gecontroleerd voor licht en donker.
- **Begroting** (sinds 15 okt 2026, branch `begroting`): per collegejaar `begroting/<jaar>-<jaar+1>.csv` met `code,debit,credit,note` en bovenin `# status: concept|vastgesteld` en `# vastgesteld: YYYY-MM-DD`. Hoort in git. `verslag/budget.py`: `Budget`, `load_budget`, `save_budget` (atomair), `find_budget` (collegejaar van de startdatum), `verslag/config.py` `edit_codes` (codes.csv toevoegen/hernoemen, behoudt commentaar). CLI: standaard automatisch, `--budget`, `--no-budget`. Waarschuwing bij conceptbegroting en bij een periode over twee collegejaren. Halfjaar = realisatie tot nu tegenover de **hele** jaarbegroting (keuze gebruiker).
- Begrotingseditor `/begroting?jaar=2026` (`templates/begroting.html`, `static/begroting.js`): live totalen (centen in JS), autosave (PUT met hele begroting + versie-hash; 409 bij wijziging elders, 423 als vastgesteld), rekenen in cellen, Enter/pijltjes, toelichting, codes toevoegen/hernoemen (`/api/codes`), vaststellen/ontgrendelen, nieuw jaar leeg/kopie/realisatie vorig jaar. API accepteert alleen JSON en geen vreemde `Origin` (CSRF). Startpagina (op verzoek, 15 okt): alleen Categorieën, Evenementen en Controles; geen resultaatkop en geen aparte begrotingssectie. Met begroting hebben het overzicht en elke categorietabel kolommen Begroot (kosten, opbrengsten, resultaat) | Realisatie (idem) | Verschil | **Van begroting**: voortgangsbalk resultaat/begroot resultaat (`budget_progress` in `web/filters.py`; 100% op 70% van de breedte met streep, grijs `--progress` tot 100%, rood `--loss` erboven bij kostenposten; opbrengstposten blauw en nooit rood behalve "verlies"; kostenpost met winst → "winst"; niet begroot → "niet begroot"). Geen grafiek meer bovenaan en geen resultaatstaafjes bij de evenementen; evenementen in één tabel met per organizer een groepsregel (naam, aantal, .tex, totalen) en onderaan het totaal (op verzoek, korter); geen begroting-.tex-links op de startpagina (wel in de zip). Schakelaar "Begroting weergeven" (`begroting=0` in de URL, `Settings.show_budget`): uit = alleen realisatie, en dan ook downloads/zip zonder begroting. Kleuren gecontroleerd met de dataviz-validator (grijs faalt alleen op chroma, bewust neutraal). Zonder begroting de oude 4 kolommen. De LaTeX-tabellen houden budget (resultaat) + difference. Getest met Playwright (scratch-venv, systeem-Chrome).
- `config.toml`: administratie-id (sandbox actief, echte administratie `382550776332945369` uitgecommentarieerd), `[[categories]]`, `[layout]` en `[events]`:
  - `label_codes`: codes waar elke regel een label hoort te hebben (C101–C104, C106, C201, C202, C204, C211, C212, C216, C217; aan te passen via /codes). Regels zonder label daaronder → waarschuwing; alle regels onder deze codes moeten optellen tot de projectrapporten (controle).
  - `max_days` (60): waarschuwing als boekingsdatum zo ver van de evenementdatum ligt.
  - `[events.organizers]`: optionele nette namen (CAD → BorrelCie, EVC → Events Committee, …).
- `events.csv` (export uit concrexit): kolommen `id,name,organizer,date` (ook `;`, `E1234` en `11-10-2026` toegestaan). Gezocht via `--events`, anders naast het script, anders (bij `--fixtures`) in de fixturesmap.
- `codes.csv`: code → regelnaam.
- `tests/fixtures/`: ingekorte API-antwoorden uit de sandbox (de bijbehorende evenementenlijst is `events.csv` naast het script). Bijgewerkt met de hand/scripts na elke wijziging in de sandbox (de MCP-antwoorden zijn groot; fixtures bevatten alleen de velden die het script gebruikt).
- `output/`: uitvoer. Oude, door het script gemaakte `events_*.tex` worden elke run opgeruimd.
- `pdflatex` is niet geïnstalleerd; de LaTeX is nooit gecompileerd in deze omgeving.

Draaien zonder token: `python3 jaarverslag.py --fixtures tests/fixtures --start 2026-09-01 --end 2027-08-31` → 10 van 10 controles in orde, geen waarschuwingen (stand 15 okt 2026).

### Evenementenlogica

- Boekregels komen uit `sales_invoices`, `external_sales_invoices`, `documents/purchase_invoices`, `documents/receipts`, `documents/general_journal_documents` (bedrag excl. btw; journaalposten ingedeeld via `account_type` van de grootboekrekening). Concepten / `new` tellen niet mee, net als in de rapporten. Datum = `date` of `invoice_date`.
- Label: eerst `[E…]` in de regelomschrijving, anders in de documentreferentie: `[E1202]` (concrexit) of los `E1206` (handmatig, `2627017 E1206`; alleen als los woord met hoofdletter E, zodat uuid's niet matchen). De referentie telt alleen als die precies één evenement noemt.
- Alle gelabelde regels tellen mee, **ongeacht de code** (de introductieborrel krijgt ook C214-kosten; een kasverschil op C502 met label telt mee bij de borrel).
- Per organizer (volgorde van eerste voorkomen in events.csv) de evenementen op datum. Gegroepeerd op de nette naam uit `[events.organizers]`: twee codes met dezelfde naam = één tabel (keuze gebruiker). Geven verschillende namen dezelfde bestandsnaam (`slug`), dan krijgt de tweede een nummer (`events_borrelcie2.tex`) + waarschuwing. De bestandsnaam staat in `Report.organizers` (`OrganizerTable`: heading, file, rows); het dashboard rekent hem niet zelf uit. Een evenement staat erin als het in de periode valt (ook zonder boekingen) of als er in de periode op geboekt is. Onbekende id's → tabel "Unknown events" + waarschuwing. Zonder events.csv: één tabel met E-nummers + waarschuwing.
- Regels met een eigen `period` geven een waarschuwing (de rapporten spreiden die).

### Controles

Projectrapporten = W&V; overzicht = W&V; categorieën = overzicht; boekregels labelcodes = projectrapporten (kosten en opbrengsten). Bij AFWIJKING exitcode 1.

### Moneybird API

v2, `https://moneybird.com/api/v2/{administration_id}/...json`: `projects` (`state:all`), `reports/revenue_by_project`, `reports/expenses_by_project`, `reports/profit_loss`, `ledger_accounts`, plus de vijf documentlijsten hierboven.

## Bekende beperkingen

- Rapporten: alleen **hele maanden**, maximaal 12.
- **Conceptfacturen** tellen niet mee in rapporten en evenementtabellen.
- Via de Moneybird-connector (MCP) kan Claude geen externe facturen of projecten verwijderen/archiveren, en geen journaalregels aanpassen (regel-id's zijn te groot voor het integer-veld). Dat moet de gebruiker in Moneybird doen.

## Sandbox in Moneybird (stand 10 okt 2026)

Administratie "Thalia Sandbox", id `500528313044830083`, eigenaar de penningmeester. Fictieve data:
- Partners Byte & Co, Kernel Consultancy, Datadijk, Recruit42; leden John Doe, Jane Smith, Michael Jackson, Sophie de Vries, Daan Bakker, Lisa Jansen, plus `Unknown Payer`; leveranciers o.a. Café De Compiler, Pizzeria Il Codice, Drukkerij Van Os, DataWolk Hosting, Wijnhandel De Druif.
- 24 externe facturen (concrexit-pushes, incl. 4 lidmaatschappen); inkomend 2627001 t/m 2627030; verkoopfacturen F2627001 t/m F2627008 (**alle concept**; de gebruiker moet ze zelf versturen, verzendmethode handmatig); memorialen "Kasverschil september" en "Kasverschil Borrel [E1208]".
- Verzonnen evenementen E1201–E1213 en E1234 (zie `events.csv`).
- Oude, lege projecten per evenement (bijv. "C101 - Maandborrel september", "C216 - Hackathon 2026") kunnen in Moneybird gearchiveerd worden. Projectnamen C102 "Borrelsnacks september" e.d. zijn nog van de oude opzet.
- Totaal 2026-2027: kosten €6.822,18, opbrengsten €187,52, resultaat −€6.634,66 (som projecten = W&V, geverifieerd).

## Open punten

1. **Beveiliging:** het token stond eerder in de docstring van `jaarverslag.py`; de gebruiker heeft het op 10 okt 2026 vervangen door `"..."` vóór de eerste commit (het zit dus niet in de git-geschiedenis). Het oude token in Moneybird intrekken als dat nog niet gebeurd is. Nooit tokens in getrackte bestanden zetten; alleen in `.env` (gitignored) of de omgeving.
2. Nog nooit een echte run tegen de live API met een token gedaan; alleen fixtures.
3. Concrexit aanpassen (pushformaat + events.csv-export) — werk van de gebruiker.
4. Tabelopmaak afstemmen op de echte LaTeX-template.
