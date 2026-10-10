# Context voor Claude Code: Thalia jaarverslag-tabellen

Communiceer met de gebruiker in het **Nederlands**. De gebruiker is penningmeester van studievereniging Thalia (Computing Science, Radboud Universiteit Nijmegen).

## Doel

Thalia boekhoudt in Moneybird. `jaarverslag.py` genereert de financiële tabellen voor het (half)jaarverslag als **LaTeX** uit Moneybird:

1. **Zes basistabellen**: overzicht (`results_overview.tex`) + per categorie Borrels, Events, General, Board, Incidental (`results_<file>.tex`). Kolommen alleen **debit, credit, result** (budget- en Difference-kolommen zijn op verzoek weggehaald; `--budget` wordt nog ingelezen maar komt niet in de uitvoer).
2. **Per organizer een tabel met zijn evenementen** (`events_<organizer>.tex`), kolommen event, date, debit, credit, result.
3. Daarnaast (nog) `balance_sheet.tex`, `partners.tex` (Invoice sent / Signed / Created) en `example.tex` dat alles inlaadt.

Debit = kosten, credit = opbrengsten, excl. btw. Result = credit − debit. Merchandisevoorraad en partnerschattingen (B.2) blijven handwerk.

## Hoe Thalia boekt

- Alles komt onder een **project** `C### - omschrijving`. Eerste cijfer = categorie/tabel (1 Borrels, 2 Events, 3 General, 4 Board, 5 Incidental), hele code = regel; projecten met dezelfde code worden opgeteld. Codes en regelnamen staan in `codes.csv`.
- **Eén project per code** (bijv. `C101 - Borrel Drink costs`), niet meer één project per evenement. Het evenement staat als label **`[E<id>]`**, met het event-id uit concrexit (de Django-backend van thalia.nu). Elk evenement staat in concrexit (eventueel ongepubliceerd).
- Thalia werkt niet met uren. Boekjaar = collegejaar, 1 september t/m 31 augustus.
- **Namen van documenten** (zoals in de echte administratie):
  - Inkomend (inkoopfacturen, bonnen, declaraties van leden): volgnummer per collegejaar, `2627001` = eerste van 2026-2027, met het event erachter als het om een evenement gaat: `2627017 E1206`. Op de regels staat het label ook: `Drankjes bar [E1208]`.
  - Verkoopfacturen: `F2627001`, met event `F2627001 E1205` (referentieveld; het echte factuurnummer kent Moneybird toe bij versturen).
  - Memorialen zonder factuur: gewone naam, eventueel met label (`Kasverschil Borrel [E1208]`).
- **Concrexit-pushes** (externe verkoopfacturen, bron `Concrexit (thalia.nu)`), één push per inschrijving/bestelling. Het label staat in de **referentie**, met een eigen id ervoor zodat de referentie uniek is (Moneybird eist unieke referenties per externe factuur, anders 422):
  - Inschrijving: `Karaoke with EVC [43494] [E1234]`, regel `Event registration Karaoke with EVC. Oct. 15, 2026. Registration date: Oct. 8, 2026.`, 0% btw.
  - Drankjes: `Sales at Shift 150 - Maandborrel September (<order-uuid>) [E1202]`, contact vaak `Unknown Payer`, regel `1x Desperados. Ordered at 16:32:29.523903`, 21% btw incl.
  - Eten: `Food Maandborrel September [9107] [E1202]`, regel `Food order by Jane Smith for Maandborrel September`, 0% btw.
  - Lidmaatschap (geen event, project C311): `Membership renewal member (year) [<uuid>]` (ook `Membership registration member (year|study)`), regel `Membership renewal member (year). Creation date: … Completion date: …` + `Date: …`, 0% btw.
  - Het eventlabel houdt de `E`; losse getallen/uuid's tussen haken zijn concrexit-id's en worden genegeerd.
- **Nog te bouwen in concrexit** (door de gebruiker): pushen naar vaste kostenplaatsen met bovenstaand formaat, en een exportknop die `events.csv` levert.

## Code

- `jaarverslag.py`: alleen standaardbibliotheek, Python 3.11+. Leest `MONEYBIRD_TOKEN` uit de omgeving.
- `config.toml`: administratie-id (sandbox actief, echte administratie `382550776332945369` uitgecommentarieerd), `[[categories]]`, `[layout]`, `[partners] codes`, en `[events]`:
  - `label_codes`: codes waar elke regel een label hoort te hebben (BorrelCie C101–C103, C104, C106, C201, C202, C204, C211, C212, C214–C217, C303, C307). Regels zonder label daaronder → waarschuwing; alle regels onder deze codes moeten optellen tot de projectrapporten (controle).
  - `max_days` (60): waarschuwing als boekingsdatum zo ver van de evenementdatum ligt.
  - `[events.organizers]`: optionele nette namen (CAD → BorrelCie, EVC → Events Committee, …).
- `events.csv` (export uit concrexit): kolommen `id,name,organizer,date` (ook `;`, `E1234` en `11-10-2026` toegestaan). Gezocht via `--events`, anders naast het script, anders (bij `--fixtures`) in de fixturesmap.
- `codes.csv`: code → regelnaam.
- `tests/fixtures/`: ingekorte API-antwoorden uit de sandbox + `events.csv`. Bijgewerkt met de hand/scripts na elke wijziging in de sandbox (de MCP-antwoorden zijn groot; fixtures bevatten alleen de velden die het script gebruikt).
- `output/`: uitvoer. Oude, door het script gemaakte `events_*.tex` worden elke run opgeruimd.
- `voorbeeld-sandbox.pdf`: verouderd voorbeeld van vóór de evenementtabellen. `pdflatex` is niet geïnstalleerd; de LaTeX is nooit gecompileerd in deze omgeving.

Draaien zonder token: `python3 jaarverslag.py --fixtures tests/fixtures --start 2026-09-01 --end 2027-08-31 --include-drafts` → 17 van 17 controles in orde, geen waarschuwingen (stand 10 okt 2026).

### Evenementenlogica

- Boekregels komen uit `sales_invoices`, `external_sales_invoices`, `documents/purchase_invoices`, `documents/receipts`, `documents/general_journal_documents` (bedrag excl. btw; journaalposten ingedeeld via `account_type` van de grootboekrekening). Concepten / `new` tellen niet mee, net als in de rapporten. Datum = `date` of `invoice_date`.
- Label: eerst `[E…]` in de regelomschrijving, anders in de documentreferentie (alleen als die precies één evenement noemt).
- Alle gelabelde regels tellen mee, **ongeacht de code** (de introductieborrel krijgt ook C214-kosten; een kasverschil op C502 met label telt mee bij de borrel).
- Per organizer (volgorde van eerste voorkomen in events.csv) de evenementen op datum. Een evenement staat erin als het in de periode valt (ook zonder boekingen) of als er in de periode op geboekt is. Onbekende id's → tabel "Unknown events" + waarschuwing. Zonder events.csv: één tabel met E-nummers + waarschuwing.
- Regels met een eigen `period` geven een waarschuwing (de rapporten spreiden die).

### Controles

Projectrapporten = W&V; overzicht = W&V; categorieën = overzicht; balans activa = passiva en regels = totaal; toename onafgesloten resultaat = resultaat (LET OP); boekregels labelcodes = projectrapporten (kosten en opbrengsten). Bij AFWIJKING exitcode 1.

### Moneybird API

v2, `https://moneybird.com/api/v2/{administration_id}/...json`: `projects` (`state:all`), `reports/revenue_by_project`, `reports/expenses_by_project`, `reports/profit_loss`, `reports/balance_sheet`, `ledger_accounts`, `sales_invoices`, `estimates`, plus de vijf documentlijsten hierboven.

## Bekende beperkingen

- Rapporten: alleen **hele maanden**, maximaal 12. Balans "begin" = eind van de maand vóór de startdatum.
- **Conceptfacturen** tellen niet mee in rapporten en evenementtabellen; `--include-drafts` werkt alleen voor de partnertabellen.
- Partnertabellen: op factuur-/offertedatum binnen de periode en projectcodes uit `[partners].codes`. Offerte `accepted` → Signed; `open`/`late` → Created.
- Via de Moneybird-connector (MCP) kan Claude geen externe facturen of projecten verwijderen/archiveren, en geen journaalregels aanpassen (regel-id's zijn te groot voor het integer-veld). Dat moet de gebruiker in Moneybird doen.

## Sandbox in Moneybird (stand 10 okt 2026)

Administratie "Thalia Sandbox", id `500528313044830083`, eigenaar de penningmeester. Fictieve data:
- Partners Byte & Co, Kernel Consultancy, Datadijk, Recruit42; leden John Doe, Jane Smith, Michael Jackson, Sophie de Vries, Daan Bakker, Lisa Jansen, plus `Unknown Payer`; leveranciers o.a. Café De Compiler, Pizzeria Il Codice, Drukkerij Van Os, DataWolk Hosting, Wijnhandel De Druif.
- 24 externe facturen (concrexit-pushes, incl. 4 lidmaatschappen); inkomend 2627001 t/m 2627030; verkoopfacturen F2627001 t/m F2627008 (**alle concept**; de gebruiker moet ze zelf versturen, verzendmethode handmatig); memorialen "Kasverschil september" en "Kasverschil Borrel [E1208]".
- Verzonnen evenementen E1201–E1213 en E1234 (zie `tests/fixtures/events.csv`).
- Oude, lege projecten per evenement (bijv. "C101 - Maandborrel september", "C216 - Hackathon 2026") kunnen in Moneybird gearchiveerd worden. Projectnamen C102 "Borrelsnacks september" e.d. zijn nog van de oude opzet.
- Totaal 2026-2027: kosten €6.822,18, opbrengsten €187,52, resultaat −€6.634,66 (som projecten = W&V, geverifieerd).

## Open punten

1. **Beveiliging:** het token stond eerder in de docstring van `jaarverslag.py`; de gebruiker heeft het op 10 okt 2026 vervangen door `"..."` vóór de eerste commit (het zit dus niet in de git-geschiedenis). Het oude token in Moneybird intrekken als dat nog niet gebeurd is. Nooit tokens in bestanden zetten; altijd via `MONEYBIRD_TOKEN` in de omgeving.
2. Nog nooit een echte run tegen de live API met een token gedaan; alleen fixtures.
3. Concrexit aanpassen (pushformaat + events.csv-export) — werk van de gebruiker.
4. Eventueel: balans/partnertabellen weglaten als ze niet meer nodig zijn; begroting inlezen; tabelopmaak afstemmen op de echte LaTeX-template.
