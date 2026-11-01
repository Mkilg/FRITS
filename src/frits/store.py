"""SQLite storage: processed files, drafts, numbered invoices and the AI audit log."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from frits import expenses, gutschriften

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    sha256 TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    received TEXT NOT NULL,
    status TEXT NOT NULL,          -- drafted | check-me | receipt
    message TEXT,
    sent_text TEXT                 -- exactly what the AI saw (already redacted), for the review screen
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    created TEXT NOT NULL,
    customer_id TEXT,
    data TEXT NOT NULL,            -- Draft as JSON
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | discarded
    invoice_number TEXT
);
-- Columns number..gross are shared with the EÜR module; keep their names and meaning.
CREATE TABLE IF NOT EXISTS invoices (
    number TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    paid_date TEXT,
    net TEXT NOT NULL,
    vat TEXT NOT NULL,
    gross TEXT NOT NULL,
    draft_id INTEGER REFERENCES drafts(id),
    due_date TEXT,
    pdf_path TEXT,
    cancels TEXT REFERENCES invoices(number),
    emailed TEXT                   -- day Fritz handed it to his mail program
);
CREATE TABLE IF NOT EXISTS quotes (
    number TEXT PRIMARY KEY,       -- AN-2026-0001
    created TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    customer_id TEXT,
    data TEXT NOT NULL,            -- Draft as JSON, same shape as invoice drafts
    status TEXT NOT NULL DEFAULT 'open',    -- open | accepted | declined
    pdf_path TEXT,
    emailed TEXT,
    draft_id INTEGER REFERENCES drafts(id)  -- set once it became an invoice draft
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    invoice_number TEXT NOT NULL REFERENCES invoices(number),
    created TEXT NOT NULL,
    level INTEGER NOT NULL,
    new_due_date TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    emailed TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    time TEXT NOT NULL,
    job_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    text_sha256 TEXT NOT NULL      -- hash of exactly what was sent, never the text itself
);
-- GoBD: an issued invoice is a record. It is never deleted and its content never changes;
-- a mistake gets a Storno. Only bookkeeping notes (paid, emailed, due date, file) may change.
CREATE TRIGGER IF NOT EXISTS invoices_never_deleted BEFORE DELETE ON invoices
BEGIN
    SELECT RAISE(ABORT, 'Rechnungen werden nie gelöscht. Bitte stornieren.');
END;
CREATE TRIGGER IF NOT EXISTS invoices_content_fixed
BEFORE UPDATE OF number, customer, issue_date, net, vat, gross, draft_id, cancels ON invoices
BEGIN
    SELECT RAISE(ABORT, 'Eine ausgestellte Rechnung ändert sich nicht. Bitte stornieren.');
END;
CREATE TRIGGER IF NOT EXISTS approved_draft_fixed
BEFORE UPDATE OF data, customer_id ON drafts WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'Diese Rechnung ist schon ausgestellt und ändert sich nicht.');
END;
"""


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    expenses.prepare(conn)
    gutschriften.prepare(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """IMMEDIATE takes the write lock up front, so two approvals can't get the same number."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def job_seen(conn: sqlite3.Connection, sha256: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE sha256 = ?", (sha256,)).fetchone() is not None


def add_job(
    conn,
    sha256: str,
    filename: str,
    status: str,
    message: str | None = None,
    sent_text: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (sha256, filename, received, status, message, sent_text)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sha256, filename, now(), status, message, sent_text),
    )
    return cur.lastrowid


def log_ai_call(conn, job_id: int | None, usage, text_sha256: str) -> None:
    conn.execute(
        "INSERT INTO audit (time, job_id, provider, model, input_tokens, output_tokens, cost_usd,"
        " text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now(),
            job_id,
            usage.provider,
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost_usd,
            text_sha256,
        ),
    )
