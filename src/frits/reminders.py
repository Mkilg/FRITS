"""Friendly payment reminders for invoices that are past their due date."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from frits import store
from frits.billing import BillingError, invoice_status
from frits.settings import Settings

REMINDER_DAYS = 7


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
    level = 1 + earlier[0]
    new_due = today + timedelta(days=REMINDER_DAYS)
    pdf = Path(folder) / f"{number}_Erinnerung_{level}.pdf"
    render_reminder(pdf, dict(row), level, settings, today, new_due, customer_address)
    cur = conn.execute(
        "INSERT INTO reminders (invoice_number, created, level, new_due_date, pdf_path)"
        " VALUES (?, ?, ?, ?, ?)",
        (number, store.now(), level, new_due.isoformat(), str(pdf)),
    )
    return cur.lastrowid, pdf, new_due
