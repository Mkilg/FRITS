"""The invoice PDF. One clean A4 layout with every field a German invoice needs (§ 14 UStG)."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from fpdf import FPDF

from frits.billing import (
    SERVICE_DATE_FALLBACK,
    Draft,
    Totals,
    service_period,
)
from frits.settings import Settings

FONTS = files("frits") / "assets" / "fonts"
log = logging.getLogger(__name__)


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
    original_paid: bool = False,
) -> Path:
    title = f"Stornorechnung zu {cancels}" if cancels else "Rechnung"
    facts = [("Rechnungsnummer", number), ("Rechnungsdatum", _de(issued))]
    if cancels:
        original = f"{cancels} vom {_de(cancels_date)}" if cancels_date else cancels
        facts.append(("Storniert wird", original))
    facts.append(("Leistungsdatum", service_period(draft.lines) or SERVICE_DATE_FALLBACK))
    if draft.customer_reference:
        facts.append(("Ihre Referenz", draft.customer_reference))
    closing = []
    if cancels and original_paid:
        closing.append(
            f"Die Rechnung {cancels} ist damit aufgehoben. Den bereits gezahlten Betrag "
            "erstatte ich Ihnen auf Ihr Konto."
        )
    elif cancels:
        closing.append(
            f"Die Rechnung {cancels} ist damit vollständig aufgehoben. Bitte nicht zahlen."
        )
    if due and not cancels:
        closing.append(
            f"Bitte überweisen Sie den Betrag bis zum {_de(due)} auf das unten genannte Konto "
            f"und geben Sie die Rechnungsnummer {number} an. Vielen Dank!"
        )
    path = _document(path, title, facts, draft, sums, settings, closing)
    _attach_e_invoice(path, number, draft, sums, settings, issued, due, cancels, cancels_date)
    return path


def _attach_e_invoice(
    path: Path, number, draft, sums, settings, issued, due, cancels, cancels_date
):
    """Turn the PDF into a ZUGFeRD / Factur-X e-invoice. If that ever fails, the plain PDF stays:
    it is still a valid invoice, and Fritz must never be stuck because of the XML."""
    from frits.erechnung import embed, invoice_xml

    try:
        xml = invoice_xml(number, draft, sums, settings, issued, due, cancels, cancels_date)
        combined = embed(Path(path).read_bytes(), xml)
    except Exception:
        log.warning(
            "E-Rechnung für %s nicht erzeugt, die PDF bleibt ohne XML.", number, exc_info=True
        )
        return
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_bytes(combined)
    temporary.replace(path)


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


def _customer_lines(draft: Draft) -> str | None:
    """The address, plus the country in capitals for customers abroad, as the post wants it."""
    from frits.customers import COUNTRIES

    address = draft.customer_address or ""
    country = draft.customer_country or "DE"
    if country != "DE":
        address = f"{address}, {COUNTRIES.get(country, country).upper()}".strip(", ")
    return address or None


def render_reminder(
    path: Path,
    invoice: dict,
    level: int,
    settings: Settings,
    issued: date,
    new_due: date,
    customer_address: str | None,
    previous: date | None = None,
) -> Path:
    """A friendly payment reminder. No fees, no threats: Fritz's customers are people he likes."""
    from frits.reminders import reminder_text

    pdf = _start(settings, invoice["customer"], customer_address)
    title = "Zahlungserinnerung" if level == 1 else f"{level}. Zahlungserinnerung"
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    pdf.cell(45, 6, "Datum")
    pdf.cell(0, 6, _de(issued), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    issue = date.fromisoformat(invoice["issue_date"])
    middle = reminder_text(
        invoice["number"], issue, eur(Decimal(invoice["gross"])), level, new_due, previous
    )
    middle[-1] = middle[-1].replace(" und geben Sie", " auf das unten genannte Konto und geben Sie")
    text = ["Guten Tag,", *middle, "Vielen Dank und freundliche Grüße", settings.name]
    for paragraph in text:
        pdf.multi_cell(0, 6, paragraph, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return _finish(pdf, path, settings)


class _Paper(FPDF):
    """A4 with Fritz's bank and tax details at the bottom of every page."""

    def __init__(self, footer_lines: list[str]) -> None:
        super().__init__(format="A4", enforce_compliance="PDFA_3B")
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


def _start(
    settings: Settings,
    customer_name: str | None,
    customer_address: str | None,
    contact: str | None = None,
) -> FPDF:
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
    head = [customer_name or "", *([f"z. Hd. {contact}"] if contact else [])]
    for line in [*head, *(customer_address or "").split(", ")]:
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
    pdf = _start(settings, draft.customer_name, _customer_lines(draft), draft.customer_contact)
    width = pdf.epw
    if draft.customer_ust_id and draft.customer_tax == "eu":
        facts = [*facts, ("USt-IdNr. Kunde", draft.customer_ust_id)]
    elif draft.customer_ust_id and draft.customer_tax == "drittland":
        facts = [*facts, ("UID / Steuernr. Kunde", draft.customer_ust_id)]

    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    for label, value in facts:
        pdf.cell(45, 6, label)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Line items
    # Several VAT rates: a small column shows which line has which (as the review page does).
    mixed = len(sums.groups) > 1
    cols = [
        width * x for x in ((0.44, 0.13, 0.15, 0.10, 0.18) if mixed else (0.50, 0.14, 0.16, 0.20))
    ]
    heads = (
        ("Leistung", "Menge", "Einzelpreis", "USt", "Betrag")
        if mixed
        else ("Leistung", "Menge", "Einzelpreis", "Betrag")
    )
    standard = sums.vat_rate if sums.vat_rate is not None else Decimal(0)
    pdf.set_font("DejaVu", "B", 10)
    for text, w in zip(heads, cols, strict=True):
        pdf.cell(w, 8, text, border="B", align="L" if text == "Leistung" else "R")
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
        if mixed:
            rate = standard if line.vat_rate is None else line.vat_rate
            pdf.cell(cols[3], 6, f"{qty(rate)} %", align="R")
        pdf.cell(cols[-1], 6, eur(line.net), align="R")
        pdf.set_xy(pdf.l_margin, top + height)
    pdf.ln(2)

    def total_row(label: str, value: Decimal, bold: bool = False) -> None:
        pdf.set_font("DejaVu", "B" if bold else "", 10)
        pdf.cell(sum(cols[:-1]), 7, label, align="R")
        pdf.cell(cols[-1], 7, eur(value), align="R", new_x="LMARGIN", new_y="NEXT")

    if sums.vat_rate is not None:
        if len(sums.groups) > 1:  # several rates: net and VAT per rate (§ 14 Abs. 4 Nr. 7, 8)
            for rate, part, _ in sums.groups:
                total_row(f"Nettobetrag zu {qty(rate)} %", part)
            for rate, _, vat in sums.groups:
                total_row(f"Umsatzsteuer {qty(rate)} %", vat)
        else:
            rate = sums.groups[0][0] if sums.groups else sums.vat_rate
            total_row("Nettobetrag", sums.net)
            total_row(f"Umsatzsteuer {qty(rate)} %", sums.vat)
    total_row("Gesamtbetrag", sums.gross, bold=True)
    pdf.ln(6)

    pdf.set_font("DejaVu", size=10)
    if sums.note:
        pdf.multi_cell(0, 6, sums.note, new_x="LMARGIN", new_y="NEXT")
    for paragraph in closing:
        pdf.multi_cell(0, 6, paragraph, new_x="LMARGIN", new_y="NEXT")
    return _finish(pdf, path, settings)
