"""De tabellen per categorie (uit de projectrapporten) en de controles op de totalen."""

from __future__ import annotations

from .amounts import CODE_RE, TOLERANCE, ZERO, Amounts, Row, category_digit, dec, fmt_plain, total


def build_results(cfg, projects, revenue, expenses, budget, warnings):
    """Groepeert projecten op code en levert {categorie-cijfer: [Row]} op."""
    row_labels: dict[str, str] = cfg["rows"]
    per_code: dict[str, Amounts] = {}
    example_name: dict[str, str] = {}

    names = {p["id"]: p["name"] for p in projects}
    rev = {r["project_id"]: dec(r["amount"]) for r in revenue}
    exp = {r["project_id"]: dec(r["amount"]) for r in expenses}

    for pid in sorted(set(rev) | set(exp), key=str):
        amounts = Amounts(debit=exp.get(pid, ZERO), credit=rev.get(pid, ZERO))
        if amounts.is_zero():
            continue
        if pid is None:
            warnings.append(
                f"Geboekt zonder project: kosten {fmt_plain(amounts.debit)}, "
                f"opbrengsten {fmt_plain(amounts.credit)}. Deze bedragen staan niet in de tabellen.")
            continue
        name = names.get(pid, f"onbekend project {pid}")
        match = CODE_RE.match(name)
        if not match:
            warnings.append(f"Project zonder code: '{name}' "
                            f"(kosten {fmt_plain(amounts.debit)}, opbrengsten {fmt_plain(amounts.credit)}). "
                            "Niet opgenomen in de tabellen.")
            continue
        code = match.group(1).upper()
        per_code.setdefault(code, Amounts())
        per_code[code] += amounts
        example_name.setdefault(code, name)

    digits = [c["digit"] for c in cfg["categories"]]
    for code in per_code:
        if category_digit(code) not in digits:
            warnings.append(f"Code {code} ('{example_name[code]}') hoort bij geen categorie in config.toml. "
                            "Niet opgenomen in de tabellen.")
        elif code not in row_labels:
            warnings.append(f"Code {code} ('{example_name[code]}') staat niet in codes.csv; "
                            "regel krijgt een tijdelijke naam.")
    for code in sorted(budget):
        if category_digit(code) not in digits:
            warnings.append(f"Begroting: code {code} hoort bij geen categorie. Niet opgenomen in de tabellen.")
        elif code not in row_labels:
            warnings.append(f"Begroting: code {code} staat niet in codes.csv; regel krijgt een tijdelijke naam.")

    hide_empty = cfg["layout"].get("hide_empty_rows", True)
    show_codes = cfg["layout"].get("show_codes", False)
    tables: dict[str, list[Row]] = {}
    for digit in digits:
        codes = sorted({c for c in row_labels if category_digit(c) == digit}
                       | {c for c in per_code if category_digit(c) == digit}
                       | {c for c in budget if category_digit(c) == digit})
        rows = []
        for code in codes:
            label = row_labels.get(code, f"Code {code} (niet in codes.csv)")
            if show_codes:
                label = f"{code} {label}"
            row = Row(code, label, budget.get(code, Amounts()), per_code.get(code, Amounts()))
            if hide_empty and row.actual.is_zero() and row.budget.is_zero():
                continue
            rows.append(row)
        tables[digit] = rows
    return tables


def run_checks(tables, overview_rows, revenue, expenses, pl):
    """Rekent de cijfers na. Geeft (naam, status, detail) terug; status is OK, AFWIJKING of LET OP."""
    checks = []

    def check(name, expected, actual, label_expected, label_actual, soft=False):
        diff = actual - expected
        if abs(diff) <= TOLERANCE:
            checks.append((name, "OK", f"{fmt_plain(actual)}"))
        else:
            checks.append((name, "LET OP" if soft else "AFWIJKING",
                           f"{label_actual} {fmt_plain(actual)} vs {label_expected} {fmt_plain(expected)} "
                           f"(verschil {fmt_plain(diff)})"))

    pl_rev, pl_exp = dec(pl.get("total_revenue")), dec(pl.get("total_expenses"))
    pl_net = dec(pl["net_profit"]) if "net_profit" in pl else pl_rev - pl_exp
    grand = total(overview_rows)

    # 1. Projectrapporten tegenover de winst-en-verliesrekening (inclusief boekingen zonder project)
    sum_rev = sum((dec(r["amount"]) for r in revenue), ZERO)
    sum_exp = sum((dec(r["amount"]) for r in expenses), ZERO)
    check("Projecten: opbrengsten = W&V", pl_rev, sum_rev, "W&V", "projecten")
    check("Projecten: kosten = W&V", pl_exp, sum_exp, "W&V", "projecten")

    # 2. Overzichtstabel tegenover de winst-en-verliesrekening
    check("Overzicht: opbrengsten = W&V", pl_rev, grand.actual.credit, "W&V", "overzicht")
    check("Overzicht: kosten = W&V", pl_exp, grand.actual.debit, "W&V", "overzicht")
    check("Overzicht: resultaat = W&V", pl_net, grand.actual.result, "W&V", "overzicht")

    # 3. Alle regels in alle categorietabellen opgeteld tegenover het overzicht
    every_row = total([r for rows in tables.values() for r in rows])
    check("Categorieen: opbrengsten = overzicht", grand.actual.credit, every_row.actual.credit, "overzicht", "regels")
    check("Categorieen: kosten = overzicht", grand.actual.debit, every_row.actual.debit, "overzicht", "regels")
    check("Categorieen: begroting = overzicht", grand.budget.result, every_row.budget.result, "overzicht", "regels")

    return checks
