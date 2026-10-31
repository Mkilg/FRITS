"""Expenses (Ausgaben): what Fritz paid for his business.

Each expense is one receipt. We store the gross amount (what was actually paid) and the
VAT rate printed on the receipt. Net and VAT are computed from those two, never typed in,
so they can't disagree. Money is always ``Decimal`` and stored in SQLite as text, so
there are no rounding surprises like 0.1 + 0.2 = 0.30000000000000004.

The ``date`` of an expense is the day it was *paid*. The EÜR counts money when it
actually leaves the account (Abflussprinzip), not when the invoice was written.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CENT = Decimal("0.01")


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    """Plain-language name shown to Fritz."""
    euer_line: str
    """Name of the matching line in the Anlage EÜR, as ELSTER shows it."""
    deductible_share: Decimal = Decimal(1)
    """Share of the net amount the tax office accepts. Business meals: only 70 %."""


# Several categories may share one EÜR line; the summary adds them up per line.
CATEGORIES: dict[str, Category] = {
    c.key: c
    for c in [
        Category("waren", "Material und Waren", "Waren, Rohstoffe und Hilfsstoffe"),
        Category("fremdleistungen", "Aufträge an andere Firmen", "Bezogene Fremdleistungen"),
        Category(
            "gwg",
            "Geräte bis 800 € netto (z. B. Laptop-Zubehör)",
            "Aufwendungen für geringwertige Wirtschaftsgüter",
        ),
        Category("telekommunikation", "Telefon und Internet", "Aufwendungen für Telekommunikation"),
        Category(
            "reisekosten",
            "Geschäftsreisen (Hotel, Bahn, Tickets)",
            "Übernachtungs- und Reisenebenkosten bei Geschäftsreisen",
        ),
        Category("fortbildung", "Kurse und Fachbücher", "Fortbildungskosten"),
        Category(
            "beratung",
            "Steuerberatung und Buchhaltung",
            "Rechts- und Steuerberatung, Buchführung",
        ),
        Category("miete", "Miete für Büro oder Werkstatt", "Miete/Pacht für Geschäftsräume"),
        Category(
            "bewirtung",
            "Geschäftsessen",
            "Bewirtungsaufwendungen",
            deductible_share=Decimal("0.7"),
        ),
        Category(
            "software",
            "Software und Abos",
            "Übrige unbeschränkt abziehbare Betriebsausgaben",
        ),
        Category(
            "buero",
            "Büromaterial und Porto",
            "Übrige unbeschränkt abziehbare Betriebsausgaben",
        ),
        Category(
            "sonstiges",
            "Sonstiges",
            "Übrige unbeschränkt abziehbare Betriebsausgaben",
        ),
    ]
}

VAT_RATES = (Decimal(0), Decimal("0.07"), Decimal("0.19"))


@dataclass(frozen=True)
class Expense:
    date: date
    vendor: str
    description: str
    gross: Decimal
    vat_rate: Decimal | None
    """0.19, 0.07, 0 or None when the receipt shows no VAT (e.g. a Kleinunternehmer seller)."""
    category: str
    source_file: str | None = None
    """The dropped file this came from (photo of a ticket, PDF bill), for checking later."""
    id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gross, Decimal):
            raise TypeError("gross must be a Decimal, e.g. Decimal('12.50')")
        if self.gross < 0:
            raise ValueError("gross must not be negative")
        if self.vat_rate is not None and self.vat_rate not in VAT_RATES:
            raise ValueError(f"vat_rate must be one of 0, 0.07, 0.19 or None, got {self.vat_rate}")
        if self.category not in CATEGORIES:
            raise ValueError(
                f"unknown category {self.category!r}; choose one of {', '.join(CATEGORIES)}"
            )

    @property
    def net(self) -> Decimal:
        """Amount without VAT, rounded to the cent."""
        if not self.vat_rate:
            return self.gross
        return (self.gross / (1 + self.vat_rate)).quantize(CENT, ROUND_HALF_UP)

    @property
    def vat(self) -> Decimal:
        """VAT contained in the gross amount (Vorsteuer)."""
        return self.gross - self.net


_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    vendor TEXT NOT NULL,
    description TEXT NOT NULL,
    gross TEXT NOT NULL,
    vat_rate TEXT,
    category TEXT NOT NULL,
    source_file TEXT
)
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def init_db(db_path: str | Path) -> None:
    """Create the ``expenses`` table if it isn't there yet. Safe to call every start."""
    _connect(db_path).close()


def add_expense(db_path: str | Path, expense: Expense) -> int:
    """Save an expense and return its new id."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO expenses (date, vendor, description, gross, vat_rate, category,"
                " source_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    expense.date.isoformat(),
                    expense.vendor,
                    expense.description,
                    str(expense.gross),
                    None if expense.vat_rate is None else str(expense.vat_rate),
                    expense.category,
                    expense.source_file,
                ),
            )
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_expenses(db_path: str | Path, year: int | None = None) -> list[Expense]:
    """All expenses, oldest first. With ``year``, only those paid in that year."""
    sql = (
        "SELECT id, date, vendor, description, gross, vat_rate, category, source_file FROM expenses"
    )
    params: tuple = ()
    if year is not None:
        sql += " WHERE date >= ? AND date < ?"
        params = (f"{year:04d}-01-01", f"{year + 1:04d}-01-01")
    sql += " ORDER BY date, id"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        Expense(
            id=row[0],
            date=date.fromisoformat(row[1]),
            vendor=row[2],
            description=row[3],
            gross=Decimal(row[4]),
            vat_rate=None if row[5] is None else Decimal(row[5]),
            category=row[6],
            source_file=row[7],
        )
        for row in rows
    ]


def delete_expense(db_path: str | Path, expense_id: int) -> bool:
    """Remove one expense. Returns False if there was no expense with that id."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        return cur.rowcount > 0
    finally:
        conn.close()
