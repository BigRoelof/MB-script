#!/usr/bin/env python3
"""Dashboard voor de jaarverslag-tabellen van Thalia.

Toont alles wat jaarverslag.py uitrekent op één pagina, met downloadknoppen voor de
LaTeX-tabellen, en een editor voor de begroting. Draait alleen op je eigen computer (127.0.0.1).

Gebruik:
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # eenmalig
    cp .env.example .env && chmod 600 .env   # token invullen, alleen nodig voor live gegevens
    .venv/bin/python dashboard.py     # daarna naar http://127.0.0.1:5000

Zonder token kun je de testdata uit tests/fixtures bekijken. De code staat in web/.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from verslag.moneybird import load_dotenv
from web import create_app

app = create_app()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--fixtures", type=Path, default=app.config["FIXTURES"],
                   help="map met testdata (standaard tests/fixtures)")
    p.add_argument("--debug", action="store_true", help="herladen bij codewijzigingen")
    args = p.parse_args()
    load_dotenv()
    app.config["FIXTURES"] = args.fixtures
    # Alleen lokaal bereikbaar: het dashboard toont financiële gegevens zonder inlog
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
