"""De bedrag-parser van de begrotingseditor (static/begroting.js), via Node als dat er is."""

import json
import shutil
import subprocess

import pytest

from verslag.config import PROJECT_DIR

CASES = {"1500": 150000, "1.500": 150000, "1.234,56": 123456, "1,234.56": 123456, "12,50": 1250,
         "€ 12,50": 1250, "4*375": 150000, "12*(15+3,50)": 22200, "−50": -5000, "": 0,
         "abc": None, "1/0": None, "3+": None}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js niet geïnstalleerd")
def test_parse_cents():
    script = f"""
const src = require("fs").readFileSync({json.dumps(str(PROJECT_DIR / "static" / "begroting.js"))}, "utf8");
const a = src.indexOf("  function parseNumber"), b = src.indexOf("  // ---------- Nieuwe begroting");
const parseCents = new Function(src.slice(a, b) + "; return parseCents;")();
const out = {{}};
for (const t of {json.dumps(list(CASES))}) {{ const v = parseCents(t); out[t] = Number.isNaN(v) ? null : v; }}
console.log(JSON.stringify(out));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == CASES
