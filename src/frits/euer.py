"""EÜR (Einnahmen-Überschuss-Rechnung): the yearly summary for the tax office.

It answers one question: how much came in, how much went out, what is left (Gewinn).
Money counts in the year it was actually paid (Zufluss- und Abflussprinzip):
an invoice counts when the customer paid it, an expense when Fritz paid it.

The result is grouped by the lines of the official form "Anlage EÜR", using the line
names ELSTER shows, so Fritz (or his tax advisor) can copy the numbers across.
Line numbers change from year to year, so we use names, not numbers.

Two tax modes:

* ``kleinunternehmer``: no VAT on invoices. Income and costs count with their full
  (gross) amount; VAT on receipts cannot be reclaimed and is simply part of the cost.
* ``regelbesteuerung``: invoices include VAT. Net income and the VAT collected are
  separate lines, costs count net and the VAT paid on receipts is its own line
  (Vorsteuer). Umsatzsteuer paid to the tax office after a Voranmeldung is an expense
  of its own (category ``ust_finanzamt``, see frits.ustva).

This is a helper, not tax advice: Fritz checks the numbers before filing.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

from frits import gutschriften
from frits.expenses import CATEGORIES, CENT, VAT_PAYMENT, list_expenses

TaxMode = Literal["kleinunternehmer", "regelbesteuerung"]
TAX_MODES: tuple[str, ...] = ("kleinunternehmer", "regelbesteuerung")

LINE_KLEINUNTERNEHMER = "Betriebseinnahmen als umsatzsteuerlicher Kleinunternehmer"
LINE_NET_INCOME = "Umsatzsteuerpflichtige Betriebseinnahmen"
LINE_TAX_FREE = (
    "Umsatzsteuerfreie, nicht umsatzsteuerbare Betriebseinnahmen sowie Betriebseinnahmen, "
    "für die der Leistungsempfänger die Umsatzsteuer nach § 13b UStG schuldet"
)
LINE_VAT_COLLECTED = "Vereinnahmte Umsatzsteuer"
LINE_VORSTEUER = "Gezahlte Vorsteuerbeträge"


@dataclass(frozen=True)
class EuerLine:
    kind: Literal["income", "expense"]
    label: str
    """Line name on the Anlage EÜR."""
    amount: Decimal


@dataclass
class EuerSummary:
    year: int
    tax_mode: str
    lines: list[EuerLine] = field(default_factory=list)
    invoice_count: int = 0
    credit_count: int = 0
    """Gutschriften the customers wrote; counted apart from Fritz's own invoices."""
    expense_count: int = 0

    @property
    def income_lines(self) -> list[EuerLine]:
        return [line for line in self.lines if line.kind == "income"]

    @property
    def expense_lines(self) -> list[EuerLine]:
        return [line for line in self.lines if line.kind == "expense"]

    @property
    def income_total(self) -> Decimal:
        return sum((line.amount for line in self.income_lines), Decimal(0))

    @property
    def expense_total(self) -> Decimal:
        return sum((line.amount for line in self.expense_lines), Decimal(0))

    @property
    def profit(self) -> Decimal:
        """Gewinn (or Verlust, if negative)."""
        return self.income_total - self.expense_total


def _paid_invoices(db_path: str | Path, year: int) -> list[tuple[Decimal, Decimal, Decimal]]:
    """(net, vat, gross) of every invoice and Gutschrift paid in ``year``. Unpaid ones don't
    count yet."""
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'invoices'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT net, vat, gross FROM invoices"
            " WHERE paid_date IS NOT NULL AND paid_date >= ? AND paid_date < ?"
            " ORDER BY paid_date",
            (f"{year:04d}-01-01", f"{year + 1:04d}-01-01"),
        ).fetchall()
        # Gutschriften the customer wrote count like paid invoices.
        credits = gutschriften.listing(
            conn, column="paid_date", start=date(year, 1, 1), end=date(year, 12, 31)
        )
        rows += [(c.net, c.vat, c.gross) for c in credits]
    finally:
        conn.close()
    return [(Decimal(net), Decimal(vat), Decimal(gross)) for net, vat, gross in rows]


def _paid_credit_count(db_path: str | Path, year: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return len(
            gutschriften.listing(
                conn, column="paid_date", start=date(year, 1, 1), end=date(year, 12, 31)
            )
        )
    finally:
        conn.close()


def _belongs_to_last_year(expense) -> bool:
    return expense.category == VAT_PAYMENT and expense.date.month == 1 and expense.date.day <= 10


def summarize(db_path: str | Path, year: int, tax_mode: TaxMode) -> EuerSummary:
    """Build the EÜR for one calendar year from the invoices and expenses tables."""
    if tax_mode not in TAX_MODES:
        raise ValueError(f"tax_mode must be one of {', '.join(TAX_MODES)}, got {tax_mode!r}")
    regel = tax_mode == "regelbesteuerung"

    # Plain dicts keep insertion order, so lines appear in a stable, readable order.
    income: dict[str, Decimal] = {}
    invoices = _paid_invoices(db_path, year)
    for net, vat, gross in invoices:
        if regel and vat == 0 and net != 0:
            # Reverse Charge in the EU, customers outside the EU, 0 % lines: no German VAT,
            # so the Anlage EÜR wants them on their own line.
            income[LINE_TAX_FREE] = income.get(LINE_TAX_FREE, Decimal(0)) + net
        elif regel:
            income[LINE_NET_INCOME] = income.get(LINE_NET_INCOME, Decimal(0)) + net
            income[LINE_VAT_COLLECTED] = income.get(LINE_VAT_COLLECTED, Decimal(0)) + vat
        else:
            income[LINE_KLEINUNTERNEHMER] = income.get(LINE_KLEINUNTERNEHMER, Decimal(0)) + gross

    costs: dict[str, Decimal] = {c.euer_line: Decimal(0) for c in CATEGORIES.values()}
    vorsteuer = Decimal(0)
    expenses = [e for e in list_expenses(db_path, year=year) if not _belongs_to_last_year(e)]
    # The Umsatzsteuer for December or the 4th quarter is due on 10 January. Paid by then,
    # it counts for the old year (regelmäßig wiederkehrende Ausgabe, § 11 Abs. 2 Satz 2 EStG).
    expenses += [e for e in list_expenses(db_path, year=year + 1) if _belongs_to_last_year(e)]
    for exp in expenses:
        cat = CATEGORIES[exp.category]
        base = exp.net if regel else exp.gross
        costs[cat.euer_line] += (base * cat.deductible_share).quantize(CENT, ROUND_HALF_UP)
        if regel:
            vorsteuer += exp.vat
    if regel and vorsteuer:
        costs[LINE_VORSTEUER] = vorsteuer

    summary = EuerSummary(
        year=year,
        tax_mode=tax_mode,
        expense_count=len(expenses),
    )
    summary.credit_count = _paid_credit_count(db_path, year)
    paid = sum(1 for _, _, gross in invoices if gross >= 0)  # refunds aren't invoices
    summary.invoice_count = paid - summary.credit_count
    summary.lines += [EuerLine("income", label, amt) for label, amt in income.items()]
    summary.lines += [EuerLine("expense", label, amt) for label, amt in costs.items() if amt]
    return summary


def _euro(amount: Decimal) -> str:
    """German number format: 1.234,56"""
    text = f"{amount.quantize(CENT, ROUND_HALF_UP):,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def write_csv(summary: EuerSummary, path: str | Path) -> Path:
    """Save the summary as a CSV that opens cleanly in Excel or Numbers (German settings)."""
    path = Path(path)
    # utf-8-sig and ";" are what German Excel expects, so umlauts and columns come out right.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Art", "Zeile Anlage EÜR", "Betrag (EUR)"])
        for line in summary.income_lines:
            writer.writerow(["Einnahme", line.label, _euro(line.amount)])
        writer.writerow(["Summe", "Summe Betriebseinnahmen", _euro(summary.income_total)])
        for line in summary.expense_lines:
            writer.writerow(["Ausgabe", line.label, _euro(line.amount)])
        writer.writerow(["Summe", "Summe Betriebsausgaben", _euro(summary.expense_total)])
        writer.writerow(["Ergebnis", "Gewinn/Verlust", _euro(summary.profit)])
    return path


def write_pdf(summary: EuerSummary, path: str | Path) -> Path:
    """Save the summary as a one-page PDF to print or hand to a tax advisor."""
    from fpdf import FPDF  # imported here so the rest of FRITS starts fast

    mode = "Kleinunternehmer" if summary.tax_mode == "kleinunternehmer" else "Regelbesteuerung"
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Einnahmen-Überschuss-Rechnung {summary.year}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        6,
        f"Steuerart: {mode}  |  {summary.invoice_count} bezahlte Rechnungen, "
        + (f"{summary.credit_count} Gutschriften, " if summary.credit_count else "")
        + f"{summary.expense_count} Ausgaben",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    def section(title: str, lines: list[EuerLine], total_label: str, total: Decimal) -> None:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        if not lines:
            pdf.cell(0, 6, "(keine)", new_x="LMARGIN", new_y="NEXT")
        for line in lines:
            pdf.cell(140, 6, line.label)
            pdf.cell(0, 6, f"{_euro(line.amount)} EUR", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(140, 7, total_label, border="T")
        pdf.cell(0, 7, f"{_euro(total)} EUR", border="T", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    section("Betriebseinnahmen", summary.income_lines, "Summe", summary.income_total)
    section("Betriebsausgaben", summary.expense_lines, "Summe", summary.expense_total)

    pdf.set_font("Helvetica", "B", 13)
    result = "Gewinn" if summary.profit >= 0 else "Verlust"
    pdf.cell(140, 9, result)
    pdf.cell(0, 9, f"{_euro(summary.profit)} EUR", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0,
        4,
        "Erstellt mit FRITS. Hilfe zum Ausfüllen der Anlage EÜR, keine Steuerberatung. "
        "Bitte vor dem Absenden in ELSTER prüfen.",
    )
    path = Path(path)
    pdf.output(str(path))
    return path
