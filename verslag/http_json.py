"""JSON ophalen over HTTP, voor Moneybird en concrexit: opnieuw proberen bij rate limits en storingen."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import VerslagError


# Zo vaak opnieuw proberen na een rate limit, en na een serverfout of netwerkfout
MAX_RATE_LIMITED = 5
MAX_RETRIES = 3


class HTTPStatusError(VerslagError):
    """De server gaf een foutcode (4xx, of 5xx na de nieuwe pogingen)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def get_json(url: str, headers: dict[str, str], service: str, path: str):
    """Eén GET-verzoek. Bij een rate limit (429) wacht hij zo lang als de server vraagt; bij een
    serverfout (5xx), time-out of verbroken verbinding probeert hij het nog een paar keer.
    Dat kan veilig: een GET verandert niets. `service` en `path` zijn alleen voor de meldingen."""
    rate_limited = failed = 0
    while True:
        req = urllib.request.Request(url, headers={**headers, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 429:
                if rate_limited == MAX_RATE_LIMITED:
                    raise HTTPStatusError(f"Te vaak rate limited op {path}", 429)
                rate_limited += 1
                wait = int(err.headers.get("Retry-After", "30"))
                print(f"  {service} rate limit, {wait}s wachten...", file=sys.stderr)
                time.sleep(wait)
                continue
            if err.code >= 500 and failed < MAX_RETRIES:
                failed += 1
                _retry_pause(service, f"fout {err.code}", path, failed)
                continue
            body = err.read().decode("utf-8", "replace")[:500]
            raise HTTPStatusError(f"{service} gaf fout {err.code} op {path}: {body}", err.code)
        except OSError as err:   # geen verbinding, time-out, verbinding verbroken (ook URLError)
            reason = getattr(err, "reason", None) or err
            if failed < MAX_RETRIES:
                failed += 1
                _retry_pause(service, str(reason), path, failed)
                continue
            raise VerslagError(f"{service} is niet bereikbaar ({reason}). Controleer je internetverbinding.")


def post_form(url: str, data: dict[str, str], service: str, path: str):
    """Eén POST met formuliervelden, JSON terug. Zonder nieuwe pogingen: een POST kan iets veranderen
    (een inlogcode is maar één keer geldig)."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:500]
        raise HTTPStatusError(f"{service} gaf fout {err.code} op {path}: {body}", err.code)
    except OSError as err:
        reason = getattr(err, "reason", None) or err
        raise VerslagError(f"{service} is niet bereikbaar ({reason}). Controleer je internetverbinding.")


def _retry_pause(service: str, reason: str, path: str, attempt: int) -> None:
    wait = 2 ** attempt   # 2, 4, 8 seconden
    print(f"  {service}: {reason} op {path}, over {wait}s opnieuw...", file=sys.stderr)
    time.sleep(wait)
