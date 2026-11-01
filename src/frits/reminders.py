"""Friendly payment reminders for invoices that are past their due date."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from frits import store
from frits.billing import BillingError, invoice_status
from frits.settings import Settings

REMINDER_DAYS = 7
MAX_LEVEL = 3  # the third is the "letzte freundliche Erinnerung"


def _de(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def reminder_text(
    number: str, issued: date, gross: str, level: int, new_due: date, previous: date | None
) -> list[str]:
    """The middle of a reminder, the same in the letter and the e-mail.

    Each level says a little more, never with fees or threats: the first assumes the
    invoice got lost, the second names the earlier reminder, the third and later ones say
    plainly that it is the last friendly one and offer to talk about instalments. Any
    reminder after the due date puts the customer in default (§ 286 Abs. 1 BGB); FRITS
    never adds interest or the 40-€ flat fee, Fritz can decide that himself.
    """
    amount = f"Rechnung {number} vom {_de(issued)} über {gross}"
    pay = (
        f"Bitte überweisen Sie den Betrag bis zum {_de(new_due)} und geben Sie die "
        f"Rechnungsnummer {number} an."
    )
    if level == 1:
        return [
            (
                f"meine {amount} ist noch offen. Vielleicht ist sie im Alltag untergegangen, "
                "das passiert."
            ),
            pay + " Falls Sie schon bezahlt haben, ist dieses Schreiben erledigt.",
        ]
    since = f" am {_de(previous)}" if previous else ""
    if level == 2:
        return [
            (
                f"ich hatte Sie{since} an meine {amount} erinnert. Bis heute ist leider keine "
                "Zahlung bei mir eingegangen."
            ),
            pay + " Falls sich das mit Ihrer Zahlung gekreuzt hat, ist dieses Schreiben erledigt.",
        ]
    return [
        (
            f"meine {amount} ist trotz Erinnerung{since} noch offen. Dies ist meine letzte "
            "freundliche Erinnerung."
        ),
        pay
        + (
            " Wenn es gerade schwierig ist, melden Sie sich bitte bei mir. Dann finden wir "
            "eine Lösung, zum Beispiel eine Zahlung in Raten."
        ),
    ]


def create_reminder(
    conn,
    number: str,
    settings: Settings,
    folder: Path,
    customer_address: str | None,
    today: date | None = None,
):
    from frits.render import render_reminder

    today = today or date.today()
    row = conn.execute(
        "SELECT i.*, c.number AS cancelled_by FROM invoices i"
        " LEFT JOIN invoices c ON c.cancels = i.number WHERE i.number = ?",
        (number,),
    ).fetchone()
    if row is None:
        raise BillingError(f"Rechnung {number} gibt es nicht.")
    status = invoice_status(row, today)
    if status == "offen":
        raise BillingError(
            f"Rechnung {number} ist noch nicht fällig. Erinnern geht ab dem Tag danach."
        )
    if status != "überfällig":
        raise BillingError(f"Rechnung {number} ist nicht mehr offen.")
    earlier = conn.execute(
        "SELECT COUNT(*), MAX(new_due_date) FROM reminders WHERE invoice_number = ?", (number,)
    ).fetchone()
    if earlier[1] and today <= date.fromisoformat(earlier[1]):
        # The customer was given a new date; the next, sharper level only makes sense
        # once that date has passed (and a second letter the same day reads as pressure).
        raise BillingError(
            f"Zu {number} läuft noch die letzte Erinnerung: Zahlungsziel "
            f"{date.fromisoformat(earlier[1]):%d.%m.%Y}. Danach kannst du nachhaken."
        )
    if earlier[0] >= MAX_LEVEL:
        # After the "last friendly reminder" another one would only repeat it. What comes
        # next (a call, a court reminder, interest for companies) is Fritz's decision.
        raise BillingError(
            f"Zu {number} gingen schon {MAX_LEVEL} Erinnerungen raus. Unter Rechnungen steht "
            "bei „Wie weiter?“, was jetzt hilft."
        )
    level = 1 + earlier[0]
    new_due = today + timedelta(days=REMINDER_DAYS)
    last = conn.execute(
        "SELECT MAX(created) FROM reminders WHERE invoice_number = ?", (number,)
    ).fetchone()[0]
    previous = date.fromisoformat(last[:10]) if last else None
    pdf = Path(folder) / f"{number}_Erinnerung_{level}.pdf"
    render_reminder(pdf, dict(row), level, settings, today, new_due, customer_address, previous)
    cur = conn.execute(
        "INSERT INTO reminders (invoice_number, created, level, new_due_date, pdf_path)"
        " VALUES (?, ?, ?, ?, ?)",
        (number, store.now(), level, new_due.isoformat(), str(pdf)),
    )
    return cur.lastrowid, pdf, new_due
