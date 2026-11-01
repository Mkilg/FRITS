"""Quotes (Angebote). Same lines and prices as invoices, own numbers, and one click to bill them."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from frits import store
from frits.billing import BillingError, Draft, _slug, draft_totals, new_draft
from frits.settings import Settings

VALID_DAYS = 30


def next_quote_number(conn, year: int) -> str:
    row = conn.execute(
        "SELECT number FROM quotes WHERE number LIKE ? ORDER BY number DESC LIMIT 1",
        (f"AN-{year}-%",),
    ).fetchone()
    last = int(row["number"].rsplit("-", 1)[1]) if row else 0
    return f"AN-{year}-{last + 1:04d}"


def create_quote(conn, draft: Draft, settings: Settings, folder: Path, today: date | None = None):
    from frits.render import render_quote

    today = today or date.today()
    if not settings.is_complete:
        raise BillingError("Deine eigenen Angaben (Name, Adresse, IBAN, Stundensatz) fehlen noch.")
    if not draft.customer_id or not draft.customer_address:
        raise BillingError("Kunde mit Adresse fehlt.")
    if not draft.lines or any(line.unit_price is None for line in draft.lines):
        raise BillingError("Jede Position im Angebot braucht einen Preis.")
    valid = today + timedelta(days=VALID_DAYS)
    with store.transaction(conn):
        number = next_quote_number(conn, today.year)
        pdf = Path(folder) / f"{number}_{_slug(draft.customer_name)}.pdf"
        conn.execute(
            "INSERT INTO quotes (number, created, valid_until, customer_id, data, pdf_path)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                number,
                today.isoformat(),
                valid.isoformat(),
                draft.customer_id,
                draft.to_json(),
                str(pdf),
            ),
        )
        render_quote(pdf, number, draft, draft_totals(draft, settings), settings, today, valid)
    return number, pdf


def list_quotes(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM quotes ORDER BY number DESC").fetchall()
    return [{**dict(r), "draft": Draft.from_json(r["data"])} for r in rows]


def accept_quote(conn, number: str) -> int:
    """The customer said yes: the quote becomes an invoice draft Fritz can adjust after the work."""
    row = conn.execute("SELECT * FROM quotes WHERE number = ?", (number,)).fetchone()
    if row is None:
        raise BillingError(f"Angebot {number} gibt es nicht.")
    if row["draft_id"]:
        return row["draft_id"]
    draft = Draft.from_json(row["data"])
    draft.source_file = f"Angebot {number}"
    draft.questions = []
    draft.warnings = []
    with store.transaction(conn):
        draft_id = new_draft(conn, draft)
        conn.execute(
            "UPDATE quotes SET status = 'accepted', draft_id = ? WHERE number = ?",
            (draft_id, number),
        )
    return draft_id


def decline_quote(conn, number: str) -> None:
    conn.execute(
        "UPDATE quotes SET status = 'declined' WHERE number = ? AND status = 'open'", (number,)
    )
