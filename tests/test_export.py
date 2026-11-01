"""Buchungsliste and Verfahrensdokumentation for the tax adviser."""

import csv
from datetime import date
from decimal import Decimal

from pypdf import PdfReader
from test_ustva import _invoice, _settings

from frits import gutschriften, store
from frits.billing import cancel, mark_paid
from frits.euer import summarize
from frits.expenses import VAT_PAYMENT, Expense, add_expense, delete_expense
from frits.export import buchungen, buchungsliste, verfahrensdoku
from frits.settings import Workspace


def _num(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _year(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings().save(ws.settings_file)
    conn = store.connect(ws.db)
    a = _invoice(conn, tmp_path, date(2026, 3, 2), ("Moderation", "1000", None), ("Text", "200", 7))
    b = _invoice(
        conn, tmp_path, date(2026, 5, 4), ("Beitrag", "500", None), where="eu", ust_id="AT1"
    )
    c = _invoice(conn, tmp_path, date(2026, 6, 1), ("Moderation", "300", None))
    for number, day in ((a, date(2026, 3, 20)), (b, date(2026, 5, 30)), (c, date(2026, 6, 9))):
        mark_paid(conn, number, day)
    storno, _ = cancel(conn, c, _settings(), tmp_path / "r", date(2026, 6, 15))
    mark_paid(conn, storno, date(2026, 6, 20))  # refunded
    _invoice(conn, tmp_path, date(2026, 12, 1), ("Offen", "999", None))  # unpaid: not listed
    gutschriften.add(
        conn,
        customer_id="kunde",
        customer="Sender AG",
        their_number="G-77",
        issue_date=date(2026, 8, 1),
        gross=Decimal("119.00"),
        vat_rate=Decimal(19),
        paid_date=date(2026, 8, 5),
    )
    add_expense(
        ws.db,
        Expense(
            date(2026, 4, 1),
            "Bahn",
            "Ticket",
            Decimal("53.50"),
            Decimal("0.07"),
            "reisekosten",
            "ticket.png",
        ),
    )
    gone = add_expense(ws.db, Expense(date(2026, 4, 2), "Doppelt", "x", Decimal(10), None, "buero"))
    delete_expense(ws.db, gone)
    add_expense(
        ws.db, Expense(date(2027, 1, 9), "Finanzamt", "USt Q4", Decimal(200), None, VAT_PAYMENT)
    )
    return ws


def test_buchungsliste_adds_up_to_the_euer(tmp_path):
    ws = _year(tmp_path)
    path = buchungsliste(ws, 2026, tmp_path / "out")
    assert path.name == "Buchungsliste_2026.csv"
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    income = sum(_num(r["Netto (EUR)"]) for r in rows if r["Einnahme/Ausgabe"] == "Einnahme")
    vat_in = sum(
        _num(r["USt/Vorsteuer (EUR)"]) for r in rows if r["Einnahme/Ausgabe"] == "Einnahme"
    )
    costs = sum(_num(r["Netto (EUR)"]) for r in rows if r["Einnahme/Ausgabe"] == "Ausgabe")
    vorsteuer = sum(
        _num(r["USt/Vorsteuer (EUR)"]) for r in rows if r["Einnahme/Ausgabe"] == "Ausgabe"
    )
    summary = summarize(ws.db, 2026, "regelbesteuerung")
    assert income + vat_in == summary.income_total
    assert costs + vorsteuer == summary.expense_total
    texts = [r["Buchungstext"] for r in rows]
    assert not any("Offen" in t or "Doppelt" in t for t in texts)
    assert any("Finanzamt" in t for t in texts)  # paid by 10 January: counts for 2026
    # One row per VAT rate, the refund negative, the credit and the receipt file named.
    first = [r for r in rows if r["Belegnummer"] == "2026-0001"]
    assert sorted(r["USt-Satz (%)"] for r in first) == ["19", "7"]
    assert any(
        r["Buchungstext"].startswith("Storno") and r["Brutto (EUR)"].startswith("-") for r in rows
    )
    assert any(r["Belegnummer"] == "GS G-77" and r["USt-Satz (%)"] == "19" for r in rows)
    assert any(r["Belegdatei"] == "ticket.png" for r in rows)
    eu = next(r for r in rows if r["Belegnummer"] == "2026-0002")
    assert eu["Zeile Anlage EÜR"].startswith("Umsatzsteuerfreie")
    assert [b.day for b in buchungen(ws, 2026)] == sorted(b.day for b in buchungen(ws, 2026))


def test_kleinunternehmer_list_uses_gross_amounts(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings(tax_mode="kleinunternehmer").save(ws.settings_file)
    store.connect(ws.db)
    add_expense(
        ws.db,
        Expense(date(2026, 4, 1), "Copyshop", "Druck", Decimal("11.90"), Decimal("0.19"), "buero"),
    )
    (row,) = buchungen(ws, 2026)
    assert (row.net, row.vat, row.rate) == (Decimal("11.90"), Decimal(0), None)
    assert summarize(ws.db, 2026, "kleinunternehmer").expense_total == row.net


def test_verfahrensdoku_describes_the_real_setup(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings(vat_basis="soll", vat_period="monat").save(ws.settings_file)
    path = verfahrensdoku(ws, 2026, tmp_path / "out", date(2027, 2, 1))
    text = " ".join(" ".join(p.extract_text() for p in PdfReader(path).pages).split())
    assert "Verfahrensdokumentation 2026" in text
    assert "monatlich" in text and "Soll-Versteuerung" in text
    assert "8 Jahre" in text and "keine Steuerberatung" in text


def test_euer_page_offers_both_files(tmp_path):
    from fastapi.testclient import TestClient
    from test_web import TODAY

    from frits.web.app import create_app

    ws = _year(tmp_path)
    app = create_app(ws, nlp_loader=lambda: None, today=lambda: TODAY)
    with TestClient(app) as client:
        assert "/euer/2026/buchungsliste.csv" in client.get("/euer?year=2026").text
        listing = client.get("/euer/2026/buchungsliste.csv")
        assert listing.status_code == 200 and "Belegnummer" in listing.content.decode("utf-8-sig")
        doku = client.get("/euer/2026/verfahrensdoku.pdf")
        assert doku.headers["content-type"] == "application/pdf"
