"""The short emails that go with an invoice, a quote or a reminder. Formal "Sie" for customers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from frits.customers import Customer
from frits.render import eur
from frits.settings import Settings


def _hello(customer: Customer | None) -> str:
    if customer and customer.company and customer.name and customer.name != customer.company:
        return f"Guten Tag {customer.name},"
    return "Guten Tag,"


def _de(day: str | date) -> str:
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return day.strftime("%d.%m.%Y")


def invoice_email(invoice: dict, customer: Customer | None, settings: Settings):
    subject = f"Rechnung {invoice['number']}"
    lines = [
        _hello(customer),
        "",
        (
            f"anbei erhalten Sie meine Rechnung {invoice['number']} über "
            f"{eur(Decimal(invoice['gross']))}."
        ),
    ]
    if invoice.get("due_date"):
        lines.append(f"Bitte überweisen Sie den Betrag bis zum {_de(invoice['due_date'])}.")
    lines += ["", "Vielen Dank für den Auftrag und freundliche Grüße", settings.name]
    return subject, "\n".join(lines)


def quote_email(quote: dict, gross: Decimal, customer: Customer | None, settings: Settings):
    subject = f"Angebot {quote['number']}"
    body = "\n".join(
        [
            _hello(customer),
            "",
            (
                f"wie besprochen sende ich Ihnen mein Angebot {quote['number']} über {eur(gross)}. "
                f"Es gilt bis zum {_de(quote['valid_until'])}."
            ),
            "Bei Fragen melden Sie sich gern.",
            "",
            "Freundliche Grüße",
            settings.name,
        ]
    )
    return subject, body


def reminder_email(
    invoice: dict,
    new_due: date,
    customer: Customer | None,
    settings: Settings,
    level: int = 1,
    previous: date | None = None,
):
    from frits.reminders import reminder_text

    subject = f"Zahlungserinnerung zu Rechnung {invoice['number']}"
    if level > 1:
        subject = f"{level}. {subject}"
    middle = reminder_text(
        invoice["number"],
        date.fromisoformat(invoice["issue_date"]),
        eur(Decimal(invoice["gross"])),
        level,
        new_due,
        previous,
    )
    middle = [line.replace("ist dieses Schreiben", "ist diese E-Mail") for line in middle]
    body = "\n".join(
        [
            _hello(customer),
            "",
            *middle,
            "Die Erinnerung hängt als PDF an.",
            "",
            "Freundliche Grüße",
            settings.name,
        ]
    )
    return subject, body
