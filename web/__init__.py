"""De webapp van het dashboard (Flask). Gestart via dashboard.py.

- common   instellingen uit de URL (periode, bron, begroting aan/uit) en het laden van de gegevens
- filters  opmaak in de templates (bedragen, datums) en de voortgangsbalk
- pages    de startpagina en de downloads (.tex, zip)
- budget   de begrotingseditor en de API waarmee die opslaat
"""

from __future__ import annotations

from flask import Flask

from verslag.budget import BUDGET_DIR
from verslag.config import PROJECT_DIR


def create_app() -> Flask:
    # root_path = de projectmap, zodat templates/ en static/ daar gevonden worden
    app = Flask(__name__, root_path=str(PROJECT_DIR))
    app.config["FIXTURES"] = PROJECT_DIR / "tests" / "fixtures"
    app.config["BUDGET_DIR"] = BUDGET_DIR
    app.config["CODES"] = PROJECT_DIR / "codes.csv"
    app.config["TEMPLATES_AUTO_RELOAD"] = True  # aanpassingen aan de template meteen zichtbaar

    from . import budget, filters, pages
    filters.register(app)
    app.register_blueprint(pages.bp)
    app.register_blueprint(budget.bp)
    return app
