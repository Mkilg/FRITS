"""The invoice PDF. One clean A4 layout with every field a German invoice needs (§ 14 UStG)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from fpdf import FPDF

from frits.billing import KLEINUNTERNEHMER_NOTE, Draft, Totals
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
) -> Path:
    title = f"Stornorechnung zu {cancels}" if cancels else "Rechnung"
    facts = [("Rechnungsnummer", number), ("Rechnungsdatum", _de(issued))]
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


def _start(settings: Settings, customer_name: str | None, customer_address: str | None) -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=25)
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
    # Footer: bank and tax details
    sender = settings.company or settings.name
    pdf.set_y(-40)
    pdf.set_font("DejaVu", size=8)
    tax_id = (
        f"USt-IdNr. {settings.ust_id}"
        if settings.ust_id
        else f"Steuernummer {settings.steuernummer}"
    )
    footer = [
        f"{sender} · {settings.address} · {settings.email} · {settings.phone}".strip(" ·"),
        f"{settings.bank} · IBAN {settings.iban} · BIC {settings.bic}".strip(" ·"),
        tax_id,
    ]
    for text in footer:
        pdf.cell(0, 4.5, text, align="C", new_x="LMARGIN", new_y="NEXT")

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
    facts = list(facts)
    dates = [line.date for line in draft.lines if line.date]
    if dates:
        period = dates[0] if len(set(dates)) == 1 else f"{dates[0]} bis {dates[-1]}"
        facts.append(("Leistungsdatum", period))
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
        label = line.description + (f" ({line.date})" if line.date else "")
        pdf.cell(cols[0], 7, label[:60])
        pdf.cell(cols[1], 7, f"{qty(line.quantity)} {line.unit}", align="R")
        pdf.cell(cols[2], 7, eur(line.unit_price), align="R")
        pdf.cell(cols[3], 7, eur(line.net), align="R", new_x="LMARGIN", new_y="NEXT")
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
