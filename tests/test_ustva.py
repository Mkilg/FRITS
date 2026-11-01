"""Umsatzsteuer-Voranmeldung: Kennzahlen, Ist/Soll, Storno, and the payment in the EÜR."""

from datetime import date
from decimal import Decimal

from frits import store
from frits.billing import Draft, Line, approve, cancel, mark_paid, new_draft
from frits.euer import summarize
from frits.expenses import VAT_PAYMENT, Expense, add_expense
from frits.settings import Settings
from frits.ustva import bounds, current, voranmeldung


def _settings(**changes) -> Settings:
    base = {
        "name": "Fritz Beispiel",
        "address": "Musterweg 1, 10115 Berlin",
        "iban": "DE02120300000000202051",
        "steuernummer": "12/345/67890",
        "ust_id": "DE123456789",
        "tax_mode": "regelbesteuerung",
        "vat_rate": Decimal(19),
        "hourly_rate": Decimal(80),
    }
    return Settings(**(base | changes))


def _invoice(conn, tmp_path, day, *lines, where="", ust_id=None):
    draft = Draft(
        customer_id="kunde",
        customer_name="Sender AG",
        customer_address="Funkweg 2, 50667 Köln",
        lines=[
            Line(
                text,
                day.isoformat(),
                Decimal(1),
                "pauschal",
                Decimal(net),
                vat_rate=None if rate is None else Decimal(rate),
            )
            for text, net, rate in lines
        ],
        questions=[],
        warnings=[],
        source_file="notiz.txt",
        customer_tax=where,
        customer_ust_id=ust_id,
    )
    number, _ = approve(conn, new_draft(conn, draft), _settings(), tmp_path / "r", day)
    return number


def test_kennzahlen_per_rate_and_country(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    day = date(2026, 7, 14)
    a = _invoice(conn, tmp_path, day, ("Moderation", "1000.60", None), ("Artikel", "300", 7))
    b = _invoice(conn, tmp_path, day, ("Beitrag", "500", None), where="eu", ust_id="ATU12345678")
    c = _invoice(conn, tmp_path, day, ("Interview", "800", None), where="drittland")
    for number in (a, b, c):
        mark_paid(conn, number, date(2026, 8, 1))
    add_expense(
        db,
        Expense(
            date(2026, 9, 3), "Bahn", "Ticket", Decimal("119.00"), Decimal("0.19"), "reisekosten"
        ),
    )

    m = voranmeldung(db, _settings(), 2026, 3)
    assert m.title == "3. Quartal 2026" and m.due == date(2026, 10, 12)  # 10.10. is a Saturday
    got = {f.kz: (f.amount, f.tax) for f in m.felder}
    assert got == {
        "81": (Decimal(1000), Decimal("190.00")),  # whole euros, cents dropped
        "86": (Decimal(300), Decimal("21.00")),
        "21": (Decimal(500), None),
        "45": (Decimal(800), None),
        "66": (Decimal("19.00"), None),
    }
    assert m.eu_customers and m.zahllast == Decimal("192.00")
    assert not voranmeldung(db, _settings(), 2026, 2).felder


def test_ist_counts_when_paid_soll_when_written(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    number = _invoice(conn, tmp_path, date(2026, 3, 30), ("Moderation", "1000", None))
    mark_paid(conn, number, date(2026, 4, 2))
    assert voranmeldung(db, _settings(), 2026, 3, "monat", "soll").feld("81").amount == 1000
    assert voranmeldung(db, _settings(), 2026, 3, "monat", "ist").feld("81") is None
    assert voranmeldung(db, _settings(), 2026, 4, "monat", "ist").feld("81").amount == 1000


def test_storno_takes_the_amount_back(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    number = _invoice(conn, tmp_path, date(2026, 1, 15), ("Moderation", "1000", None))
    storno, _ = cancel(conn, number, _settings(), tmp_path / "r", date(2026, 4, 20))
    assert voranmeldung(db, _settings(), 2026, 1, basis="soll").feld("81").amount == 1000
    assert voranmeldung(db, _settings(), 2026, 2, basis="soll").feld("81").amount == -1000
    # Ist: neither was paid, so neither counts; once refunded, the refund counts.
    assert not voranmeldung(db, _settings(), 2026, 2).felder
    mark_paid(conn, number, date(2026, 2, 1))
    mark_paid(conn, storno, date(2026, 5, 5))
    assert voranmeldung(db, _settings(), 2026, 1).feld("81").amount == 1000
    assert voranmeldung(db, _settings(), 2026, 2).feld("81").amount == -1000


def test_kleinunternehmer_invoices_are_left_out(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    draft = Draft("k", "Kunde", "Weg 1, 10115 Berlin", [], [], [], "n.txt")
    draft.lines = [Line("Arbeit", "2026-05-02", Decimal(1), "pauschal", Decimal(100))]
    klein = _settings(tax_mode="kleinunternehmer")
    approve(conn, new_draft(conn, draft), klein, tmp_path / "r", date(2026, 5, 2))
    assert not voranmeldung(db, _settings(), 2026, 2, basis="soll").felder


def test_periods():
    assert bounds(2026, "quartal", 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert bounds(2028, "monat", 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert current(date(2026, 11, 1), "quartal") == (2026, 3)
    assert current(date(2027, 1, 5), "monat") == (2026, 12)
    assert current(date(2027, 1, 5), "quartal") == (2026, 4)


def test_vat_payment_is_an_expense_and_january_counts_for_the_old_year(tmp_path):
    db = tmp_path / "frits.db"
    store.connect(db)
    pay = Expense(
        date(2027, 1, 8), "Finanzamt", "USt Q4", Decimal("400.00"), Decimal("0.19"), VAT_PAYMENT
    )
    assert pay.vat_rate is None  # a tax payment carries no Vorsteuer
    add_expense(db, pay)
    add_expense(
        db,
        Expense(date(2027, 2, 10), "Finanzamt", "USt Jan", Decimal("50.00"), None, VAT_PAYMENT),
    )
    line = "An das Finanzamt gezahlte und ggf. verrechnete Umsatzsteuer"
    old = {x.label: x.amount for x in summarize(db, 2026, "regelbesteuerung").expense_lines}
    new = {x.label: x.amount for x in summarize(db, 2027, "regelbesteuerung").expense_lines}
    assert old[line] == Decimal("400.00") and new[line] == Decimal("50.00")


def test_page_shows_kennzahlen_and_books_the_payment(tmp_path, monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from test_web import TODAY

    from frits import expenses
    from frits.settings import Workspace
    from frits.web.app import create_app

    ws = Workspace(tmp_path / "FRITS").ensure()
    _settings(setup_done=True).save(ws.settings_file)
    conn = store.connect(ws.db)
    number = _invoice(conn, tmp_path, date(2026, 8, 3), ("Moderation", "1000", None))
    mark_paid(conn, number, date(2026, 9, 1))
    app = create_app(ws, nlp_loader=lambda: None, today=lambda: TODAY)
    with TestClient(app) as client:
        page = client.get("/ustva").text
        assert "3. Quartal 2026" in page and "12.10.2026" in page
        assert '<code id="kz-81">1000</code>' in page and "190,00 €" in page
        client.post(
            "/ustva/2026/3/bezahlt",
            data={"token": app.state.token, "paid": "2026-10-09"},
            follow_redirects=True,
        )
        (paid,) = expenses.list_expenses(ws.db)
        assert (paid.gross, paid.category) == (Decimal("190.00"), VAT_PAYMENT)
        assert "eingetragen am 09.10.2026" in client.get("/ustva").text
        client.post(
            "/ustva/art",
            data={"token": app.state.token, "period": "monat", "basis": "soll"},
            follow_redirects=True,
        )
        assert Settings.load(ws.settings_file).vat_period == "monat"
        assert "Oktober 2026" in client.get("/ustva").text
        replace(Settings.load(ws.settings_file), tax_mode="kleinunternehmer").save(ws.settings_file)
        assert "brauchst du keine" in client.get("/ustva").text
