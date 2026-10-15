"""De LaTeX-uitvoer: resultaat-, begrotings- en evenemententabellen en example.tex."""

from __future__ import annotations

from decimal import Decimal

from .amounts import Amounts, Row, fmt_plain, total
from .events import EventRow


LATEX_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                 "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}",
                 "\\": r"\textbackslash{}"}


def tex(text: str) -> str:
    return "".join(LATEX_SPECIAL.get(ch, ch) for ch in str(text))


def fmt(value: Decimal) -> str:
    s = fmt_plain(value)
    return "$-$" + s[1:] if s.startswith("-") else s


def results_table(rows: list[Row], total_label="TOTAL", with_budget=False) -> str:
    """Resultatentabel. Met begroting erbij: account, budget, debit, credit, result, difference
    (budget = begroot resultaat, difference = result - budget)."""
    if with_budget:
        spec, ncols = "rrrrr", 6
        group = (" & & \\multicolumn{3}{c}{\\textsc{result} (\\texteuro)} & \\\\\n"
                 "\\cmidrule(lr){3-5}\n"
                 "\\textsc{account} & \\textsc{budget} & \\textsc{debit} & \\textsc{credit} & "
                 "\\textsc{result} & \\textsc{difference} \\\\\n")
    else:
        spec, ncols = "rrr", 4
        group = (" & \\multicolumn{3}{c}{\\textsc{result} (\\texteuro)} \\\\\n"
                 "\\cmidrule(lr){2-4}\n"
                 "\\textsc{account} & \\textsc{debit} & \\textsc{credit} & \\textsc{result} \\\\\n")
    head = (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        f"\\begin{{tabularx}}{{\\textwidth}}{{@{{}}>{{\\raggedright\\arraybackslash}}X {spec}@{{}}}}\n"
        "\\toprule\n" + group + "\\midrule\n"
    )

    def line(r: Row, bold=False):
        cells = [tex(r.label), fmt(r.actual.debit), fmt(r.actual.credit), fmt(r.actual.result)]
        if with_budget:
            cells = [cells[0], fmt(r.budget.result), *cells[1:], fmt(r.difference)]
        if bold:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        return " & ".join(cells) + " \\\\\n"

    body = "".join(line(r) for r in rows)
    if not rows:
        body = f"\\multicolumn{{{ncols}}}{{l}}{{\\emph{{No bookings in this period}}}} \\\\\n"
    return (head + body + "\\midrule\n" + line(total(rows, total_label), bold=True)
            + "\\bottomrule\n\\end{tabularx}\n")


def budget_table(rows: list[Row], notes: dict[str, str] | None = None, total_label="TOTAL",
                 skip_empty=True) -> str:
    """De begroting zelf (voor de ALV): account, debit, credit, result, met toelichting als die er is."""
    if skip_empty:
        rows = [r for r in rows if not r.budget.is_zero()]
    notes = notes or {}
    with_notes = any(notes.get(r.code) for r in rows)
    spec = "rrr" + (" >{\\raggedright\\arraybackslash}p{0.3\\textwidth}" if with_notes else "")
    ncols = 5 if with_notes else 4
    head = (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        f"\\begin{{tabularx}}{{\\textwidth}}{{@{{}}>{{\\raggedright\\arraybackslash}}X {spec}@{{}}}}\n"
        "\\toprule\n"
        " & \\multicolumn{3}{c}{\\textsc{budget} (\\texteuro)}" + (" &" if with_notes else "") + " \\\\\n"
        "\\cmidrule(lr){2-4}\n"
        "\\textsc{account} & \\textsc{debit} & \\textsc{credit} & \\textsc{result}"
        + (" & \\textsc{explanation}" if with_notes else "") + " \\\\\n"
        "\\midrule\n"
    )

    def line(r: Row, bold=False):
        cells = [tex(r.label), fmt(r.budget.debit), fmt(r.budget.credit), fmt(r.budget.result)]
        if bold:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        if with_notes:
            cells.append(tex(notes.get(r.code, "")))
        return " & ".join(cells) + " \\\\\n"

    body = "".join(line(r) for r in rows)
    if not rows:
        body = f"\\multicolumn{{{ncols}}}{{l}}{{\\emph{{Nothing budgeted}}}} \\\\\n"
    return (head + body + "\\midrule\n" + line(total(rows, total_label), bold=True)
            + "\\bottomrule\n\\end{tabularx}\n")


def event_table(rows: list[EventRow]) -> str:
    grand = Amounts()
    body = ""
    for r in rows:
        a = r.amounts
        grand += a
        date = f"{r.date:%d-%m-%Y}" if r.date else ""
        body += f"{tex(r.name)} & {date} & {fmt(a.debit)} & {fmt(a.credit)} & {fmt(a.result)} \\\\\n"
    if not rows:
        body = "\\multicolumn{5}{l}{\\emph{No events in this period}} \\\\\n"
    return (
        "% Gegenereerd door jaarverslag.py -- niet met de hand aanpassen\n"
        "\\begin{tabularx}{\\textwidth}{@{}>{\\raggedright\\arraybackslash}X l rrr@{}}\n"
        "\\toprule\n"
        "\\textsc{event} & \\textsc{date} & \\textsc{debit} & \\textsc{credit} & \\textsc{result} \\\\\n"
        "\\midrule\n" + body + "\\midrule\n"
        f"\\textbf{{TOTAL}} & & \\textbf{{{fmt(grand.debit)}}} & \\textbf{{{fmt(grand.credit)}}} & "
        f"\\textbf{{{fmt(grand.result)}}} \\\\\n"
        "\\bottomrule\n\\end{tabularx}\n"
    )


EXAMPLE_DOC = r"""\documentclass[a4paper,11pt]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{textcomp}
\begin{document}
\section*{Financial report %(period)s}
\subsection*{Results overview}
{\small\input{results_overview}}
%(sections)s
%(event_sections)s
%(budget_sections)s
\end{document}
"""
