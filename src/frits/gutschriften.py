"""Gutschriften: customers who bill themselves (many broadcasters do).

The customer writes the document and pays; Fritz writes no invoice of his own (§ 14c UStG).
The money is still his income, so it has to show up in the EÜR, in the
Umsatzsteuer-Voranmeldung and in the § 19 limits. This module keeps those received
Gutschriften, with the same GoBD rules as invoices: never deleted, never rewritten, a
mistake is struck out (and the strike is visible).

Fritz types what the Gutschrift says: the total (gross) and the VAT rate on it. Net and VAT
are worked out, so they can't disagree.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
KINDS = ("inland", "eu", "drittland")

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    customer TEXT NOT NULL,          -- name as on the Gutschrift
    their_number TEXT NOT NULL,      -- the customer's number for it
    issue_date TEXT NOT NULL,
    net TEXT NOT NULL,
    vat TEXT NOT NULL,
    gross TEXT NOT NULL,
    vat_rate TEXT,                   -- '19', '7', '0'; NULL when Fritz is Kleinunternehmer
    kind TEXT NOT NULL DEFAULT 'inland',   -- inland | eu | drittland
    paid_date TEXT,
    created TEXT NOT NULL,
    struck TEXT                      -- set instead of deleting (GoBD)
)
""",
    """
CREATE TRIGGER IF NOT EXISTS credits_never_deleted BEFORE DELETE ON credits
BEGIN SELECT RAISE(ABORT, 'Gutschriften werden nie gelöscht, nur gestrichen (GoBD).'); END
""",
    """
CREATE TRIGGER IF NOT EXISTS credits_content_fixed
BEFORE UPDATE OF customer_id, customer, their_number, issue_date, net, vat, gross, vat_rate, kind
ON credits
BEGIN SELECT RAISE(ABORT, 'Eine erfasste Gutschrift ändert sich nicht (GoBD).'); END
""",
)


class CreditError(ValueError):
    """Something Fritz typed doesn't fit; the message says what, in plain German."""


@dataclass(frozen=True)
class Credit:
    id: int
    customer_id: str
    customer: str
    their_number: str
    issue_date: date
    net: Decimal
    vat: Decimal
    gross: Decimal
    vat_rate: Decimal | None
    kind: str
    paid_date: date | None
    struck: str | None

    @property
    def label(self) -> str:
        return f"Gutschrift {self.their_number} von {self.customer}"


def prepare(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        conn.execute(statement)


def split(gross: Decimal, rate: Decimal | None) -> tuple[Decimal, Decimal]:
    """(net, vat) from the total and the rate printed on the Gutschrift."""
    if not rate:
        return gross, Decimal("0.00")
    net = (gross * 100 / (100 + rate)).quantize(CENT, ROUND_HALF_UP)
    return net, gross - net


def add(
    conn: sqlite3.Connection,
    *,
    customer_id: str,
    customer: str,
    their_number: str,
    issue_date: date,
    gross: Decimal,
    vat_rate: Decimal | None,
    kind: str = "inland",
    paid_date: date | None = None,
) -> int:
    their_number = " ".join(their_number.split())
    if not their_number:
        raise CreditError("Bitte die Nummer der Gutschrift eintragen, sie steht auf dem Beleg.")
    if gross <= 0:
        raise CreditError("Der Betrag muss größer als 0 sein.")
    if kind not in KINDS:
        raise CreditError("Unbekannter Kundenort.")
    if vat_rate is not None and vat_rate not in (Decimal(19), Decimal(7), Decimal(0)):
        raise CreditError("Umsatzsteuer auf der Gutschrift: 19 %, 7 % oder 0 %.")
    if kind != "inland" and vat_rate:
        raise CreditError(
            "Bei Kunden im Ausland steht auf der Gutschrift keine deutsche Umsatzsteuer. "
            "Steht doch welche drauf, frag bitte beim Kunden nach."
        )
    if paid_date and paid_date < issue_date - timedelta(days=366):
        raise CreditError("Das Zahldatum liegt mehr als ein Jahr vor der Gutschrift. Vertippt?")
    dup = conn.execute(
        "SELECT 1 FROM credits WHERE customer_id = ? AND their_number = ? AND struck IS NULL",
        (customer_id, their_number),
    ).fetchone()
    if dup:
        raise CreditError(f"Die Gutschrift {their_number} ist schon erfasst.")
    gross = gross.quantize(CENT, ROUND_HALF_UP)
    net, vat = split(gross, vat_rate)
    cur = conn.execute(
        "INSERT INTO credits (customer_id, customer, their_number, issue_date, net, vat, gross,"
        " vat_rate, kind, paid_date, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            customer_id,
            customer,
            their_number,
            issue_date.isoformat(),
            str(net),
            str(vat),
            str(gross),
            None if vat_rate is None else str(vat_rate),
            kind,
            paid_date.isoformat() if paid_date else None,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return int(cur.lastrowid)


def mark_paid(conn: sqlite3.Connection, credit_id: int, paid: date) -> bool:
    cur = conn.execute(
        "UPDATE credits SET paid_date = ? WHERE id = ? AND paid_date IS NULL AND struck IS NULL",
        (paid.isoformat(), credit_id),
    )
    return cur.rowcount > 0


def strike(conn: sqlite3.Connection, credit_id: int) -> bool:
    cur = conn.execute(
        "UPDATE credits SET struck = ? WHERE id = ? AND struck IS NULL",
        (datetime.now().isoformat(timespec="seconds"), credit_id),
    )
    return cur.rowcount > 0


def restore(conn: sqlite3.Connection, credit_id: int) -> bool:
    cur = conn.execute(
        "UPDATE credits SET struck = NULL WHERE id = ? AND struck IS NOT NULL", (credit_id,)
    )
    return cur.rowcount > 0


def _credit(row) -> Credit:
    return Credit(
        id=row[0],
        customer_id=row[1],
        customer=row[2],
        their_number=row[3],
        issue_date=date.fromisoformat(row[4]),
        net=Decimal(row[5]),
        vat=Decimal(row[6]),
        gross=Decimal(row[7]),
        vat_rate=None if row[8] is None else Decimal(row[8]),
        kind=row[9],
        paid_date=date.fromisoformat(row[10]) if row[10] else None,
        struck=row[11],
    )


_COLUMNS = (
    "id, customer_id, customer, their_number, issue_date, net, vat, gross, vat_rate, kind,"
    " paid_date, struck"
)


def listing(
    conn: sqlite3.Connection,
    *,
    customer_id: str | None = None,
    column: str = "issue_date",
    start: date | None = None,
    end: date | None = None,
) -> list[Credit]:
    """Gutschriften that count (not struck), newest first. ``column`` picks which date
    ``start``/``end`` (both inclusive) filter on: "issue_date" or "paid_date"."""
    if column not in ("issue_date", "paid_date"):
        raise ValueError(column)
    sql, args = f"SELECT {_COLUMNS} FROM credits WHERE struck IS NULL", []
    if customer_id is not None:
        sql += " AND customer_id = ?"
        args.append(customer_id)
    if start is not None:
        sql += f" AND {column} >= ?"
        args.append(start.isoformat())
    if end is not None:
        sql += f" AND {column} <= ?"
        args.append(end.isoformat())
    try:
        rows = conn.execute(sql + f" ORDER BY {column} DESC, id DESC", args).fetchall()
    except sqlite3.OperationalError:  # a database from before Gutschriften existed
        return []
    return [_credit(row) for row in rows]
