"""Add a column to an older database, safely when several connections open at once."""

from __future__ import annotations

import sqlite3


def spalte_dazu(conn: sqlite3.Connection, tabelle: str, spalte: str, art: str) -> None:
    """``ALTER TABLE … ADD COLUMN`` only if the column is missing.

    Every new connection checks the schema. When FRITS starts, the browser asks for several
    pages at once, so two connections can both see "missing" and both add it; the second
    one then gets "duplicate column name" (Lena Runde 67: Fehler 500 beim ersten Start).
    That is fine: the column is there, which is all we wanted."""
    spalten = {row[1] for row in conn.execute(f"PRAGMA table_info({tabelle})")}
    if spalte in spalten:
        return
    try:
        conn.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {art}")
    except sqlite3.OperationalError as fehler:
        if "duplicate column name" not in str(fehler):
            raise
