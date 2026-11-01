"""Invoices Fritz wrote before FRITS: entered with their own number and date, never backdated."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from test_ustva import _invoice, _settings

from frits import store
from frits.billing import BillingError, add_earlier, cancel, list_invoices, next_number
from frits.euer import summarize
from frits.export import buchungen
from frits.settings import Workspace
from frits.ustva import voranmeldung

TODAY = date(2026, 11, 2)


def _ws(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings().save(ws.settings_file)
    return ws, store.connect(ws.db)


def _earlier(conn, ws, **changes):
    args = {
        "number": "2026-0004",
        "customer": "Radio Süd",
        "issue_date": date(2026, 2, 10),
        "description": "Moderation Februar",
        "net": Decimal(1000),
        "tax": "19",
        "settings": _settings(),
        "paid_date": date(2026, 2, 25),
        "invoices_dir": ws.invoices,
        "today": TODAY,
    }
    return add_earlier(conn, **(args | changes))


def test_earlier_invoice_counts_in_euer_ustva_and_buchungsliste(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws)
    row = conn.execute("SELECT * FROM invoices WHERE number = '2026-0004'").fetchone()
    assert (row["net"], row["vat"], row["gross"]) == ("1000.00", "190.00", "1190.00")
    assert row["issue_date"] == "2026-02-10" and row["pdf_path"] is None

    lines = {line.label: line.amount for line in summarize(ws.db, 2026, "regelbesteuerung").lines}
    assert Decimal("1000.00") in lines.values() and Decimal("190.00") in lines.values()

    q1 = voranmeldung(ws.db, _settings(), 2026, 1, basis="soll")
    assert q1.invoices == 1
    assert any(f.tax == Decimal("190.00") for f in q1.felder)

    rows = [b for b in buchungen(ws, 2026, _settings()) if b.beleg == "2026-0004"]
    assert [(b.gross, b.vat) for b in rows] == [(Decimal("1190.00"), Decimal("190.00"))]

    listed = {i["number"]: i for i in list_invoices(conn, TODAY)}
    assert listed["2026-0004"]["earlier"] and listed["2026-0004"]["status"] == "bezahlt"


def test_frits_numbers_carry_on_after_earlier_invoices(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws)
    assert next_number(conn, 2026) == "2026-0005"
    assert _invoice(conn, tmp_path, TODAY, ("Neu", "100", None)) == "2026-0005"


def test_kleinunternehmer_and_open_earlier_invoice(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws, number="R 17/2025", tax="klein", paid_date=None, issue_date=date(2026, 9, 1))
    row = conn.execute("SELECT * FROM invoices WHERE number = 'R 17/2025'").fetchone()
    assert (row["vat"], row["gross"]) == ("0.00", "1000.00")
    assert row["due_date"] == "2026-09-15" and row["paid_date"] is None
    listed = {i["number"]: i for i in list_invoices(conn, TODAY)}
    assert listed["R 17/2025"]["status"] == "überfällig"


def test_earlier_invoice_can_be_cancelled_like_any_other(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws, tax="eu")
    storno, _ = cancel(conn, "2026-0004", _settings(), ws.invoices, TODAY)
    row = conn.execute("SELECT net, vat FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert (row["net"], row["vat"]) == ("-1000.00", "0.00")


def test_earlier_invoice_is_a_record_too(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE invoices SET net = '1' WHERE number = '2026-0004'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE drafts SET data = '{}' WHERE invoice_number = '2026-0004'")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"issue_date": date(2026, 11, 3)}, "Zukunft"),
        ({"number": "../../etc"}, "Rechnungsnummer"),
        ({"number": ""}, "Rechnungsnummer"),
        ({"customer": "  "}, "Kunden"),
        ({"net": Decimal(0)}, "Betrag"),
        ({"tax": "13"}, "besteuert"),
        ({"paid_date": date(2026, 12, 1)}, "Zahldatum"),
        ({"original": b"not a pdf"}, "PDF"),
    ],
)
def test_earlier_invoice_checks_what_fritz_typed(tmp_path, changes, message):
    ws, conn = _ws(tmp_path)
    with pytest.raises(BillingError, match=message):
        _earlier(conn, ws, **changes)
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_same_number_twice_is_refused(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws)
    with pytest.raises(BillingError, match="gibt es in FRITS schon"):
        _earlier(conn, ws)


def test_original_pdf_is_kept_next_to_the_invoices(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws, number="2025/12", original=b"%PDF-1.4 alt")
    path = conn.execute("SELECT pdf_path FROM invoices").fetchone()[0]
    assert path.endswith("Nachgetragen/2025-12_Radio-Süd.pdf")
    assert Path(path).read_bytes() == b"%PDF-1.4 alt"


def test_own_number_series_stays_in_time_order(tmp_path):
    """Tester: 2026-0002 vom 20.10. nachgetragen, obwohl 2026-0001 vom 01.11. ist."""
    ws, conn = _ws(tmp_path)
    assert _invoice(conn, tmp_path, date(2026, 11, 1), ("Neu", "100", None)) == "2026-0001"
    with pytest.raises(BillingError, match="kleinere Nummer 2026-0001"):
        _earlier(conn, ws, number="2026-0002", issue_date=date(2026, 10, 20), paid_date=None)
    with pytest.raises(BillingError, match="Lücke"):
        _earlier(conn, ws, number="2026-0007", issue_date=date(2026, 11, 2), paid_date=None)
    # Another scheme (his old one) is free: it says nothing about FRITS's order.
    _earlier(conn, ws, number="R-2026-02", issue_date=date(2026, 10, 20), paid_date=None)
    assert next_number(conn, 2026) == "2026-0002"


def test_earlier_numbers_in_own_pattern_must_fit_each_other(tmp_path):
    ws, conn = _ws(tmp_path)
    _earlier(conn, ws, number="2026-0003", issue_date=date(2026, 3, 1))
    with pytest.raises(BillingError, match="größere Nummer 2026-0003"):
        _earlier(conn, ws, number="2026-0001", issue_date=date(2026, 5, 1))
    _earlier(conn, ws, number="2026-0002", issue_date=date(2026, 2, 1))  # fits between
    _earlier(conn, ws, number="2025-0009", issue_date=date(2025, 12, 1))  # other year
    assert next_number(conn, 2026) == "2026-0004"
