#!/usr/bin/env python3
"""Zet Moneybird-data om naar LaTeX-tabellen voor het (half)jaarverslag van Thalia.

Gebruik:
    # persoonlijk API-token uit Moneybird in .env (zie .env.example) of in de omgeving
    python3 jaarverslag.py --start 2026-09-01 --end 2027-08-31

Zonder --start/--end wordt het lopende collegejaar (1 september t/m 31 augustus)
gebruikt. Alle tabellen komen als losse .tex-bestanden in de map --out
(standaard ./output) en zijn met \\input{} in het verslag op te nemen.

Alleen de Python-standaardbibliotheek is nodig (Python 3.11 of nieuwer).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from verslag.amounts import Row, fmt_plain
from verslag.budget import find_budget, load_budget
from verslag.config import load_config, validate_period
from verslag.errors import VerslagError
from verslag.events import find_events
from verslag.moneybird import fetch_data, load_dotenv, make_client
from verslag.report import build_report


def parse_args(argv=None):
    today = dt.date.today()
    year = today.year if today.month >= 9 else today.year - 1
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(year, 9, 1),
                   help="begindatum, eerste dag van een maand (standaard 1 september)")
    p.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(year + 1, 8, 31),
                   help="einddatum, laatste dag van een maand (standaard 31 augustus)")
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    p.add_argument("--codes", type=Path, default=Path(__file__).with_name("codes.csv"),
                   help="CSV met kolommen code,name (standaard codes.csv naast het script)")
    p.add_argument("--events", type=Path,
                   help="evenementenexport uit concrexit, kolommen id,name,organizer,date "
                        "(standaard events.csv naast het script, of in de --fixtures-map)")
    p.add_argument("--budget", type=Path,
                   help="begroting, CSV met kolommen code,debit,credit(,note) (standaard "
                        "begroting/<collegejaar>.csv naast het script, bijv. begroting/2026-2027.csv)")
    p.add_argument("--no-budget", action="store_true", help="geen begroting gebruiken")
    p.add_argument("--out", type=Path, default=Path("output"))
    p.add_argument("--fixtures", type=Path, help="map met opgeslagen API-antwoorden (testen zonder token)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    try:
        return run(args)
    except VerslagError as err:
        print(f"Fout: {err}", file=sys.stderr)
        return 1


def run(args) -> int:
    """Haalt de gegevens op, schrijft de tabellen en print de samenvatting. Exitcode 1 bij een afwijking."""
    validate_period(args.start, args.end)
    cfg, label_codes = load_config(args.config, args.codes)
    client = make_client(cfg, args.fixtures)

    print(f"Periode {args.start:%d-%m-%Y} t/m {args.end:%d-%m-%Y}", file=sys.stderr)
    print("Gegevens ophalen uit Moneybird...", file=sys.stderr)
    data = fetch_data(client, args.start, args.end)
    events_path = find_events(args.events, args.fixtures)
    if args.no_budget:
        budget = None
    elif args.budget:
        budget = load_budget(args.budget)
    else:
        budget = find_budget(args.start)
    report = build_report(cfg, label_codes, data, args.start, args.end, events_path, budget)
    if report.events_path:
        print(f"Evenementenlijst: {report.events_path}", file=sys.stderr)
    if budget:
        print(f"Begroting: {budget.path} ({budget.status})", file=sys.stderr)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    # Evenement- en begrotingstabellen van een vorige run weghalen (organizers of begroting kunnen veranderd zijn)
    for old in [*out.glob("events_*.tex"), *out.glob("budget_*.tex")]:
        if old.read_text(encoding="utf-8").startswith("% Gegenereerd door jaarverslag.py"):
            old.unlink()
    for filename, content in report.files.items():
        (out / filename).write_text(content, encoding="utf-8")

    # Samenvatting naar de terminal
    grand = report.grand
    print("\nResultaat per categorie:", file=sys.stderr)
    for r in [*report.overview_rows, Row("", "TOTAL", grand.budget, grand.actual)]:
        print(f"  {r.label:<34} kosten {fmt_plain(r.actual.debit):>12}  opbrengsten "
              f"{fmt_plain(r.actual.credit):>12}  resultaat {fmt_plain(r.actual.result):>12}"
              + (f"  begroot {fmt_plain(r.budget.result):>12}" if report.budget else ""), file=sys.stderr)
    print("\nControles:", file=sys.stderr)
    for name, status, detail in report.checks:
        print(f"  [{status:<9}] {name}: {detail}", file=sys.stderr)
    failed = [c for c in report.checks if c[1] == "AFWIJKING"]
    soft = [c for c in report.checks if c[1] == "LET OP"]
    ok = sum(1 for c in report.checks if c[1] == "OK")
    print(f"  Controles: {ok} van {len(report.checks)} in orde"
          + (f", {len(failed)} afwijking(en)" if failed else "")
          + (f", {len(soft)} let op" if soft else ""), file=sys.stderr)
    if report.warnings:
        print("\nLet op:", file=sys.stderr)
        for w in report.warnings:
            print(f"  - {w}", file=sys.stderr)
    print(f"\nTabellen geschreven naar {out.resolve()}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
