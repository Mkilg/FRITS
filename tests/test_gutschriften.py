"""Gutschriften: customers who bill themselves. The money must still reach EÜR, UStVA, § 19."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_web import TODAY

from frits import gutschriften, store
from frits.billing import turnover
from frits.customers import Customer, save_note
from frits.euer import LINE_NET_INCOME, LINE_TAX_FREE, LINE_VAT_COLLECTED, summarize
from frits.settings import Settings, Workspace
from frits.ustva import voranmeldung
from frits.web.app import create_app


def _settings(**changes) -> Settings:
    base = {
        "name": "Fritz Beispiel",
        "address": "Musterweg 1, 10115 Berlin",
        "iban": "DE02120300000000202051",
        "steuernummer": "12/345/67890",
        "hourly_rate": Decimal(80),
        "tax_mode": "regelbesteuerung",
        "setup_done": True,
    }
    return Settings(**(base | changes))


def _add(conn, number="GS-1", gross="1190.00", rate=19, kind="inland", paid=None, **more):
    return gutschriften.add(
        conn,
        customer_id="sender",
        customer="Sender AG",
        their_number=number,
        issue_date=more.get("issued", date(2026, 7, 1)),
        gross=Decimal(gross),
        vat_rate=None if rate is None else Decimal(rate),
        kind=kind,
        paid_date=paid,
    )


def test_credit_counts_in_euer_ustva_and_limits(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    _add(conn, paid=date(2026, 7, 20))
    _add(conn, "GS-2", "500.00", 0, "eu", paid=date(2026, 8, 3))
    _add(conn, "GS-3", "107.00", 7)  # not paid yet

    lines = {x.label: x.amount for x in summarize(db, 2026, "regelbesteuerung").income_lines}
    assert lines[LINE_NET_INCOME] == Decimal("1000.00")
    assert lines[LINE_VAT_COLLECTED] == Decimal("190.00")
    assert lines[LINE_TAX_FREE] == Decimal("500.00")

    m = voranmeldung(db, _settings(), 2026, 3)
    assert {f.kz: f.amount for f in m.felder} == {"81": 1000, "21": 500}
    soll = voranmeldung(db, _settings(), 2026, 3, basis="soll")
    assert soll.feld("86").amount == 100

    assert turnover(conn, 2026) == Decimal("1600.00")


def test_credit_is_a_record(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    credit_id = _add(conn)
    with pytest.raises(gutschriften.CreditError, match="schon erfasst"):
        _add(conn)
    with pytest.raises(sqlite3.IntegrityError, match="nie gelöscht"):
        conn.execute("DELETE FROM credits")
    with pytest.raises(sqlite3.IntegrityError, match="ändert sich nicht"):
        conn.execute("UPDATE credits SET gross = '1.00'")
    assert gutschriften.strike(conn, credit_id)
    assert gutschriften.listing(conn) == []
    _add(conn)  # struck out, so it may be entered again (correctly this time)
    assert gutschriften.restore(conn, credit_id)
    assert len(gutschriften.listing(conn)) == 2


def test_wrong_input_is_refused(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    with pytest.raises(gutschriften.CreditError, match="Ausland"):
        _add(conn, kind="drittland")
    with pytest.raises(gutschriften.CreditError, match="Nummer"):
        _add(conn, number="  ")
    with pytest.raises(gutschriften.CreditError, match="größer"):
        _add(conn, gross="0")


def test_kleinunternehmer_credit_counts_gross(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    _add(conn, gross="400.00", rate=None, paid=date(2026, 3, 1))
    summary = summarize(db, 2026, "kleinunternehmer")
    assert summary.income_total == Decimal("400.00")
    assert (summary.invoice_count, summary.credit_count) == (0, 1)


def test_entering_a_credit_on_the_customer_page(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings().save(ws.settings_file)
    save_note(ws.customers, Customer("sender", "Anna Muster", "Sender AG", gutschrift=True))
    app = create_app(ws, nlp_loader=lambda: None, today=lambda: TODAY)
    with TestClient(app) as client:
        token = app.state.token
        page = client.get("/customers/sender").text
        assert "Gutschriften von Sender AG" in page and 'name="vat_rate"' in page
        page = client.post(
            "/customers/sender/gutschriften",
            data={"token": token, "number": "HA-77", "issue_date": "2026-10-20", "gross": "238"},
            follow_redirects=True,
        ).text
        assert "Gutschrift erfasst: 238,00 €" in page and "noch nicht bezahlt" in page
        (credit,) = gutschriften.listing(store.connect(ws.db))
        assert (credit.net, credit.vat) == (Decimal("200.00"), Decimal("38.00"))
        page = client.post(
            f"/gutschriften/{credit.id}/bezahlt",
            data={"token": token, "paid": "2026-10-28", "back": "https://evil.example"},
            follow_redirects=True,
        )
        assert str(page.url).endswith("/invoices")
        assert "HA-77" in page.text and "bezahlt am 28.10.2026" in page.text
        page = client.post(
            f"/gutschriften/{credit.id}/streichen",
            data={"token": token, "back": "/customers/sender"},
            follow_redirects=True,
        ).text
        assert "Gutschrift gestrichen" in page and "Rückgängig" in page
        page = client.post("/rueckgaengig", data={"token": token}, follow_redirects=True).text
        assert "wieder da" in page and "HA-77" in page
