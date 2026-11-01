"""VAT per rate, customers abroad and the § 19 UStG limits."""

from datetime import date
from decimal import Decimal

import pytest

from frits import store
from frits.billing import (
    DRITTLAND_NOTE,
    REVERSE_CHARGE_NOTE,
    BillingError,
    Draft,
    Line,
    approve,
    blockers,
    cancel,
    draft_totals,
    limit_check,
    new_draft,
    set_customer,
)
from frits.customers import Customer, load_customers, save_note
from frits.ingest import extract_text
from frits.settings import Settings

DAY = date(2026, 11, 2)


def _settings(**changes) -> Settings:
    base = {
        "name": "Fritz Beispiel",
        "address": "Musterweg 1, 10115 Berlin",
        "iban": "DE02120300000000202051",
        "hourly_rate": Decimal(80),
        "steuernummer": "12/345/67890",
        "tax_mode": "regelbesteuerung",
        "vat_rate": Decimal(19),
    }
    return Settings(**(base | changes))


def _draft(*lines, where="", ust_id=None) -> Draft:
    return Draft(
        customer_id="kunde",
        customer_name="Sender AG",
        customer_address="Funkweg 2, 8000 Zürich",
        lines=list(lines),
        questions=[],
        warnings=[],
        source_file="notiz.txt",
        customer_tax=where,
        customer_ust_id=ust_id,
    )


def _line(description, net, rate=None):
    return Line(description, "2026-10-03", Decimal(1), "pauschal", Decimal(net), vat_rate=rate)


def test_mixed_rates_are_summed_per_rate():
    draft = _draft(
        _line("Moderation", "500"), _line("Artikel mit Nutzungsrechten", "300", Decimal(7))
    )
    sums = draft_totals(draft, _settings())
    assert sums.groups == (
        (Decimal(19), Decimal("500.00"), Decimal("95.00")),
        (Decimal(7), Decimal("300.00"), Decimal("21.00")),
    )
    assert (sums.net, sums.vat, sums.gross) == (
        Decimal("800.00"),
        Decimal("116.00"),
        Decimal("916.00"),
    )


def test_mixed_rates_on_the_pdf_and_in_the_storno(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    draft = _draft(_line("Moderation", "500"), _line("Artikel", "300", Decimal(7)))
    number, pdf = approve(conn, new_draft(conn, draft), _settings(), tmp_path / "r", DAY)
    text = " ".join(extract_text(pdf).text.split())
    assert "Nettobetrag zu 19 % 500,00 €" in text and "Nettobetrag zu 7 % 300,00 €" in text
    assert "Umsatzsteuer 7 % 21,00 €" in text and "916,00 €" in text
    storno, _ = cancel(conn, number, _settings(vat_rate=Decimal(16)), tmp_path / "r", DAY)
    row = conn.execute("SELECT vat FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert row["vat"] == "-116.00"


def test_reverse_charge_for_eu_business(tmp_path):
    draft = _draft(_line("Beitrag", "1000"), where="eu", ust_id="ATU12345678")
    settings = _settings()
    assert any("eigene USt-IdNr" in p for p in blockers(draft, settings))
    settings = _settings(ust_id="DE123456789")
    assert not blockers(draft, settings)
    sums = draft_totals(draft, settings)
    assert (sums.vat, sums.gross, sums.note) == (
        Decimal("0.00"),
        Decimal("1000.00"),
        REVERSE_CHARGE_NOTE,
    )
    conn = store.connect(tmp_path / "frits.db")
    _, pdf = approve(conn, new_draft(conn, draft), settings, tmp_path / "r", DAY)
    text = " ".join(extract_text(pdf).text.split())
    assert "Reverse Charge" in text and "ATU12345678" in text and "DE123456789" in text


def test_eu_customer_needs_a_vat_id():
    draft = _draft(_line("Beitrag", "1000"), where="eu")
    assert any("des Kunden" in p for p in blockers(draft, _settings(ust_id="DE123456789")))


def test_customer_outside_the_eu_gets_no_german_vat():
    sums = draft_totals(_draft(_line("Moderation", "800"), where="drittland"), _settings())
    assert (sums.vat, sums.note) == (Decimal("0.00"), DRITTLAND_NOTE)


def test_kleinunternehmer_ignores_rates_and_country():
    draft = _draft(_line("Artikel", "300", Decimal(7)), where="eu")
    sums = draft_totals(draft, _settings(tax_mode="kleinunternehmer"))
    assert sums.vat_rate is None and sums.vat == 0 and "§ 19" in sums.note


def test_customer_note_keeps_where_it_sits(tmp_path):
    save_note(tmp_path, Customer("sender", "Anna Muster", "Sender AG", umsatzsteuer="drittland"))
    customer = load_customers(tmp_path)[0]
    assert customer.umsatzsteuer == "drittland"
    draft = _draft(_line("Moderation", "800"))
    set_customer(draft, customer)
    assert draft.customer_tax == "drittland"


def _issued(conn, year, net):
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
        " VALUES (?, 'x', ?, ?, '0', ?)",
        (f"{year}-{net}", f"{year}-06-01", str(net), str(net)),
    )


def test_limits_for_kleinunternehmer(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    assert limit_check(conn, klein, 2026) == ([], [])
    _issued(conn, 2026, 24_000)
    problems, warnings = limit_check(conn, klein, 2026, Decimal(2000))
    assert not problems and "ab 2027" in warnings[0]
    _issued(conn, 2025, 30_000)
    assert "kein Kleinunternehmer mehr" in limit_check(conn, klein, 2026)[1][0]
    _issued(conn, 2026, 75_000)
    assert "100.000" in limit_check(conn, klein, 2026, Decimal(2000))[0][0]
    assert limit_check(conn, _settings(), 2026, Decimal(2000)) == ([], [])


def test_approval_stops_at_the_100k_limit(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    _issued(conn, 2026, 99_500)
    draft = _draft(_line("Moderation", "800"))
    with pytest.raises(BillingError, match="100.000"):
        approve(conn, new_draft(conn, draft), _settings(tax_mode="kleinunternehmer"), tmp_path, DAY)


def test_copying_an_invoice_makes_a_fresh_draft(tmp_path):
    from frits.billing import copy_invoice, load_draft

    conn = store.connect(tmp_path / "frits.db")
    draft = _draft(_line("Moderation", "500"), _line("Artikel", "300", Decimal(7)))
    number, _ = approve(conn, new_draft(conn, draft), _settings(), tmp_path / "r", DAY)
    copy, _ = load_draft(conn, copy_invoice(conn, number))
    assert [(x.description, x.unit_price, x.vat_rate, x.date) for x in copy.lines] == [
        ("Moderation", Decimal(500), None, None),
        ("Artikel", Decimal(300), Decimal(7), None),
    ]
    assert copy.tax is None and copy.source_file == f"Kopie von Rechnung {number}"
    storno, _ = cancel(conn, number, _settings(), tmp_path / "r", DAY)
    with pytest.raises(BillingError, match="Stornorechnung"):
        copy_invoice(conn, storno)
