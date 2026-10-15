"""Toegang tot de Moneybird-API (v2), of tot opgeslagen API-antwoorden om zonder token te testen."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import PROJECT_DIR


API_BASE = "https://moneybird.com/api/v2"


class MoneybirdClient:
    """Minimale client voor de Moneybird REST API (v2)."""

    def __init__(self, token: str, administration_id: str):
        self.token = token
        self.base = f"{API_BASE}/{administration_id}"

    def get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base}/{path}.json" + (f"?{query}" if query else "")
        for attempt in range(6):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt < 5:
                    wait = int(err.headers.get("Retry-After", "30"))
                    print(f"  Moneybird rate limit, {wait}s wachten...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                body = err.read().decode("utf-8", "replace")[:500]
                raise SystemExit(f"Moneybird gaf fout {err.code} op {path}: {body}")
        raise SystemExit(f"Te vaak rate limited op {path}")


class FixtureClient:
    """Leest opgeslagen API-antwoorden uit een map, om zonder token te testen."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, path: str, params: dict | None = None):
        params = params or {}
        if int(params.get("page", 1)) > 1:
            return []
        key = path.replace("/", "_")
        file = self.directory / f"{key}.json"
        if not file.exists():
            raise SystemExit(f"Fixture ontbreekt: {file}")
        return json.loads(file.read_text(encoding="utf-8"))


def paged(client, path: str, params: dict, key: str | None = None) -> list:
    """Haalt alle pagina's op. `key` is nodig bij rapporten (object met lijst)."""
    out, page = [], 1
    while True:
        data = client.get(path, {**params, "page": page, "per_page": 100})
        items = data.get(key, []) if (key and isinstance(data, dict)) else data
        out.extend(items)
        if len(items) < 100:
            return out
        page += 1


def load_dotenv(path: Path = PROJECT_DIR / ".env") -> None:
    """Zet regels als MONEYBIRD_TOKEN=... uit .env in de omgeving.

    Variabelen die al in de omgeving staan gaan voor. Het bestand staat in .gitignore.
    """
    if not path.exists():
        return
    if os.name == "posix" and path.stat().st_mode & 0o077:
        print(f"Let op: {path} is leesbaar voor andere gebruikers; maak het privé met: chmod 600 {path}",
              file=sys.stderr)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def make_client(cfg: dict, fixtures: Path | None):
    if fixtures:
        return FixtureClient(fixtures)
    token = os.environ.get("MONEYBIRD_TOKEN")
    if not token:
        raise SystemExit("Geen Moneybird-token gevonden. Zet MONEYBIRD_TOKEN=... in het bestand .env "
                         "naast het script, of als omgevingsvariabele.")
    return MoneybirdClient(token, cfg["administration_id"])


def fetch_project_reports(client, start: dt.date, end: dt.date) -> tuple[list, list]:
    """De projectrapporten over een periode van hele maanden: (opbrengsten, kosten) per project."""
    period = f"{start:%Y%m%d}..{end:%Y%m%d}"
    return (paged(client, "reports/revenue_by_project", {"period": period}, "revenue_by_project"),
            paged(client, "reports/expenses_by_project", {"period": period}, "expenses_by_project"))


def fetch_data(client, start: dt.date, end: dt.date) -> dict:
    """Haalt alles op wat het verslag nodig heeft (ruwe API-antwoorden)."""
    period = f"{start:%Y%m%d}..{end:%Y%m%d}"
    revenue, expenses = fetch_project_reports(client, start, end)
    return {
        "projects": paged(client, "projects", {"filter": "state:all"}),
        "revenue": revenue,
        "expenses": expenses,
        "pl": client.get("reports/profit_loss", {"period": period}),
        "ledger": client.get("ledger_accounts"),
        # Voor de evenementtabellen: de boekregels zelf (de rapporten kennen geen omschrijvingen)
        "documents": {
            "sales_invoices": paged(client, "sales_invoices", {"filter": f"period:{period},state:all"}),
            "external_sales_invoices": paged(client, "external_sales_invoices", {"filter": f"period:{period}"}),
            "purchase_invoices": paged(client, "documents/purchase_invoices", {"filter": f"period:{period}"}),
            "receipts": paged(client, "documents/receipts", {"filter": f"period:{period}"}),
            "general_journal_documents": paged(client, "documents/general_journal_documents",
                                               {"filter": f"period:{period}"}),
        },
    }
