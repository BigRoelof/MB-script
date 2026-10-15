"""Gedeelde fixtures: de opgeslagen Moneybird-antwoorden (sandbox, collegejaar 2026-2027)."""

import datetime as dt
import shutil
from pathlib import Path

import pytest

from verslag.config import PROJECT_DIR, load_config
from verslag.moneybird import FixtureClient, fetch_data

FIXTURES = PROJECT_DIR / "tests" / "fixtures"
START, END = dt.date(2026, 9, 1), dt.date(2027, 8, 31)


@pytest.fixture(scope="session")
def config():
    """(cfg, label_codes) uit de echte config.toml en codes.csv."""
    return load_config(PROJECT_DIR / "config.toml", PROJECT_DIR / "codes.csv")


@pytest.fixture(scope="session")
def data():
    return fetch_data(FixtureClient(FIXTURES), START, END)


@pytest.fixture
def codes_file(tmp_path) -> Path:
    """Een kopie van codes.csv om in te schrijven."""
    path = tmp_path / "codes.csv"
    shutil.copy(PROJECT_DIR / "codes.csv", path)
    return path


@pytest.fixture
def client(tmp_path, codes_file):
    """Testclient voor het dashboard, met een lege begrotingsmap en een kopie van codes.csv."""
    from web import create_app
    app = create_app()
    app.config.update(TESTING=True, FIXTURES=FIXTURES, BUDGET_DIR=tmp_path / "begroting", CODES=codes_file)
    return app.test_client()
