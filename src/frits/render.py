"""The invoice PDF. One clean A4 layout with every field a German invoice needs (§ 14 UStG)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from fpdf import FPDF

from frits.billing import (
    KLEINUNTERNEHMER_NOTE,
    SERVICE_DATE_FALLBACK,
    Draft,
    Totals,
    service_period,
)
from frits.settings import Settings

FONTS = files("frits") / "assets" / "fonts"


def eur(value: Decimal) -> str:
    """German number format: 1.234,50 €"""
    sign = "-" if value < 0 else ""
    whole, cents = f"{abs(value):.2f}".split(".")
    whole = f"{int(whole):,}".replace(",", ".")
    return f"{sign}{whole},{cents} €"


def qty(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def _de(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def render_invoice(
    path: Path,
    number: str,
    draft: Draft,
    sums: Totals,
    settings: Settings,
    issued: date,
    due: date | None,
    cancels: str | None = None,
    cancels_date: date | None = None,
) -> Path:
    title = f"Stornorechnung zu {cancels}" if cancels else "Rechnung"
    facts = [("Rechnungsnummer", number), ("Rechnungsdatum", _de(issued))]
    if cancels:
        original = f"{cancels} vom {_de(cancels_date)}" if cancels_date else cancels
        facts.append(("Storniert wird", original))
    facts.append(("Leistungsdatum", service_period(draft.lines) or SERVICE_DATE_FALLBACK))
    closing = []
    if due and not cancels:
        closing.append(
            f"Bitte überweisen Sie den Betrag bis zum {_de(due)} auf das unten genannte Konto "
            f"und geben Sie die Rechnungsnummer {number} an. Vielen Dank!"
        )
    return _document(path, title, facts, draft, sums, settings, closing)


def render_quote(
    path: Path,
    number: str,
    draft: Draft,
    sums: Totals,
    settings: Settings,
    issued: date,
    valid_until: date,
) -> Path:
    facts = [("Angebotsnummer", number), ("Datum", _de(issued))]
    if period := service_period(draft.lines):
        facts.append(("Leistungszeitraum", period))
    closing = [
        f"Dieses Angebot gilt bis zum {_de(valid_until)}. Ich freue mich auf Ihren Auftrag.",
    ]
    return _document(path, "Angebot", facts, draft, sums, settings, closing)


def render_reminder(
    path: Path,
    invoice: dict,
    level: int,
    settings: Settings,
    issued: date,
    new_due: date,
    customer_address: str | None,
) -> Path:
    """A friendly payment reminder. No fees, no threats: Fritz's customers are people he likes."""
    pdf = _start(settings, invoice["customer"], customer_address)
    title = "Zahlungserinnerung" if level == 1 else f"{level}. Zahlungserinnerung"
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    pdf.cell(45, 6, "Datum")
    pdf.cell(0, 6, _de(issued), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    issue = date.fromisoformat(invoice["issue_date"])
    text = [
        "Guten Tag,",
        (
            f"meine Rechnung {invoice['number']} vom {_de(issue)} über "
            f"{eur(Decimal(invoice['gross']))} ist noch offen. Vielleicht ist sie im Alltag "
            "untergegangen, das passiert."
        ),
        (
            f"Bitte überweisen Sie den Betrag bis zum {_de(new_due)} auf das unten genannte Konto "
            f"und geben Sie die Rechnungsnummer {invoice['number']} an. Falls Sie schon bezahlt "
            "haben, betrachten Sie dieses Schreiben bitte als erledigt."
        ),
        "Vielen Dank und freundliche Grüße",
        settings.name,
    ]
    for paragraph in text:
        pdf.multi_cell(0, 6, paragraph, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return _finish(pdf, path, settings)


class _Paper(FPDF):
    """A4 with Fritz's bank and tax details at the bottom of every page."""

    def __init__(self, footer_lines: list[str]) -> None:
        super().__init__(format="A4")
        self.footer_lines = footer_lines

    def footer(self) -> None:
        self.set_y(-38)
        self.set_font("DejaVu", size=8)
        for text in self.footer_lines:
            self.cell(0, 4.5, text, align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4.5, f"Seite {self.page_no()} von {{nb}}", align="C")


def _footer_lines(settings: Settings) -> list[str]:
    sender = settings.company or settings.name
    tax_ids = []
    if settings.steuernummer.strip():
        tax_ids.append(f"Steuernummer {settings.steuernummer.strip()}")
    if settings.ust_id.strip():
        tax_ids.append(f"USt-IdNr. {settings.ust_id.strip()}")
    return [
        line
        for line in (
            " · ".join(x for x in (sender, settings.address, settings.email, settings.phone) if x),
            " · ".join(
                x
                for x in (
                    settings.bank,
                    f"IBAN {settings.iban}" if settings.iban else "",
                    f"BIC {settings.bic}" if settings.bic else "",
                )
                if x
            ),
            " · ".join(tax_ids),
        )
        if line
    ]


def _start(settings: Settings, customer_name: str | None, customer_address: str | None) -> FPDF:
    pdf = _Paper(_footer_lines(settings))
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=42)
    pdf.add_font("DejaVu", fname=str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", style="B", fname=str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    sender = settings.company or settings.name
    pdf.set_font("DejaVu", size=8)
    pdf.cell(0, 5, f"{sender} · {settings.address}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("DejaVu", size=11)
    for line in [customer_name or "", *(customer_address or "").split(", ")]:
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    return pdf


def _finish(pdf: FPDF, path: Path, settings: Settings) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def _document(
    path: Path,
    title: str,
    facts: list[tuple[str, str]],
    draft: Draft,
    sums: Totals,
    settings: Settings,
    closing: list[str],
) -> Path:
    pdf = _start(settings, draft.customer_name, draft.customer_address)
    width = pdf.epw

    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    for label, value in facts:
        pdf.cell(45, 6, label)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Line items
    cols = [width * 0.50, width * 0.14, width * 0.16, width * 0.20]
    pdf.set_font("DejaVu", "B", 10)
    for text, w, align in zip(("Leistung", "Menge", "Einzelpreis", "Betrag"), cols, "LRRR"):
        pdf.cell(w, 8, text, border="B", align=align)
    pdf.ln()
    pdf.set_font("DejaVu", size=10)
    for line in draft.lines:
        # The whole description is printed, wrapped if needed: it is the "Art der Leistung".
        label = line.description + (f" ({service_period([line])})" if line.date else "")
        rows = pdf.multi_cell(cols[0], 6, label, align="L", dry_run=True, output="LINES")
        height = max(len(rows), 1) * 6 + 1
        if pdf.will_page_break(height):
            pdf.add_page()
        top = pdf.get_y()
        pdf.multi_cell(cols[0], 6, label, align="L", new_x="RIGHT", new_y="TOP")
        pdf.set_xy(pdf.l_margin + cols[0], top)
        pdf.cell(cols[1], 6, f"{qty(line.quantity)} {line.unit}", align="R")
        pdf.cell(cols[2], 6, eur(line.unit_price), align="R")
        pdf.cell(cols[3], 6, eur(line.net), align="R")
        pdf.set_xy(pdf.l_margin, top + height)
    pdf.ln(2)

    def total_row(label: str, value: Decimal, bold: bool = False) -> None:
        pdf.set_font("DejaVu", "B" if bold else "", 10)
        pdf.cell(sum(cols[:3]), 7, label, align="R")
        pdf.cell(cols[3], 7, eur(value), align="R", new_x="LMARGIN", new_y="NEXT")

    if sums.vat_rate is not None:
        total_row("Nettobetrag", sums.net)
        total_row(f"Umsatzsteuer {qty(sums.vat_rate)} %", sums.vat)
    total_row("Gesamtbetrag", sums.gross, bold=True)
    pdf.ln(6)

    pdf.set_font("DejaVu", size=10)
    if sums.vat_rate is None:
        pdf.multi_cell(0, 6, KLEINUNTERNEHMER_NOTE, new_x="LMARGIN", new_y="NEXT")
    for paragraph in closing:
        pdf.multi_cell(0, 6, paragraph, new_x="LMARGIN", new_y="NEXT")
    return _finish(pdf, path, settings)
