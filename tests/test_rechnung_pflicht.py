"""German invoice rules: § 14 UStG mandatory details and GoBD (records never change)."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from frits import store
from frits.billing import (
    BillingError,
    Draft,
    Line,
    approve,
    blockers,
    cancel,
    new_draft,
    service_period,
)
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
    }
    return Settings(**(base | changes))


def _line(description="Moderation Podiumsgespräch", when="2026-10-03", qty="2", price="80"):
    return Line(description, when, Decimal(qty), "Std", Decimal(price))


def _draft(*lines) -> Draft:
    return Draft(
        customer_id="sender",
        customer_name="Sender GmbH",
        customer_address="Funkstraße 2, 80331 München",
        lines=list(lines) or [_line()],
        questions=[],
        warnings=[],
        source_file="notiz.txt",
    )


def _issue(tmp_path, settings, draft=None):
    conn = store.connect(tmp_path / "frits.db")
    draft_id = new_draft(conn, draft or _draft())
    number, pdf = approve(conn, draft_id, settings, tmp_path / "rechnungen", DAY)
    return conn, number, pdf


def test_service_period_from_line_dates():
    assert service_period([_line(when="2026-10-03")]) == "03.10.2026"
    later_first = [_line(when="12.10.2026"), _line(when="2026-10-03")]
    assert service_period(later_first) == "03.10.2026 bis 12.10.2026"
    assert service_period([_line(when="Oktober 2026")]) == "Oktober 2026"
    assert service_period([_line(when=None)]) is None


def test_tax_number_or_vat_id_is_required_for_invoices():
    no_ids = _settings(steuernummer="", ust_id="")
    assert any("Steuernummer" in p for p in blockers(_draft(), no_ids))
    assert not blockers(_draft(), _settings(steuernummer="", ust_id="DE123456789"))
    quote = _draft()
    quote.kind = "quote"
    assert not blockers(quote, no_ids)


def test_pdf_has_mandatory_details(tmp_path):
    long = (
        "Recherche, Konzept und Moderation des Podiumsgesprächs zur Zukunft des Lokaljournalismus"
    )
    settings = _settings(ust_id="DE123456789")
    _, number, pdf = _issue(tmp_path, settings, _draft(_line(long, when=None)))
    text = " ".join(extract_text(pdf).text.split())
    assert number in text
    assert "Lokaljournalismus" in text  # the whole description, wrapped, not cut off
    assert "Leistungsdatum entspricht dem Rechnungsdatum" in text
    assert "Steuernummer 12/345/67890" in text and "USt-IdNr. DE123456789" in text
    assert "§ 19 UStG" in text


def test_storno_mirrors_the_original_even_after_tax_settings_change(tmp_path):
    settings = _settings(tax_mode="regelbesteuerung", vat_rate=Decimal(19))
    conn, number, _ = _issue(tmp_path, settings)
    switched = _settings(tax_mode="kleinunternehmer")
    storno, pdf = cancel(conn, number, switched, tmp_path / "rechnungen", DAY)
    row = conn.execute("SELECT * FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert (row["net"], row["vat"], row["gross"]) == ("-160.00", "-30.40", "-190.40")
    text = " ".join(extract_text(pdf).text.split())
    assert f"{number} vom 02.11.2026" in text


def test_storno_of_an_older_invoice_reads_the_rate_from_its_sums(tmp_path):
    conn, number, _ = _issue(tmp_path, _settings(tax_mode="regelbesteuerung", vat_rate=Decimal(7)))
    # Invoices from before the tax snapshot have none stored with their draft.
    conn.execute("DROP TRIGGER approved_draft_fixed")
    conn.execute("UPDATE drafts SET data = json_remove(data, '$.tax')")
    storno, _ = cancel(conn, number, _settings(), tmp_path / "rechnungen", DAY)
    row = conn.execute("SELECT vat FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert row["vat"] == "-11.20"


def test_issued_invoices_never_change_or_disappear(tmp_path):
    conn, number, _ = _issue(tmp_path, _settings())
    with pytest.raises(sqlite3.IntegrityError, match="nie gelöscht"):
        conn.execute("DELETE FROM invoices WHERE number = ?", (number,))
    with pytest.raises(sqlite3.IntegrityError, match="ändert sich nicht"):
        conn.execute("UPDATE invoices SET gross = '1.00' WHERE number = ?", (number,))
    with pytest.raises(sqlite3.IntegrityError, match="ändert sich nicht"):
        conn.execute("UPDATE drafts SET data = '{}' WHERE invoice_number = ?", (number,))
    conn.execute("UPDATE invoices SET paid_date = '2026-11-20' WHERE number = ?", (number,))


def test_an_existing_pdf_is_never_overwritten(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    folder = tmp_path / "rechnungen"
    folder.mkdir()
    (folder / "2026-0001_Sender-GmbH.pdf").write_bytes(b"Fritz's own file")
    with pytest.raises(BillingError, match="überschreibt"):
        approve(conn, new_draft(conn, _draft()), _settings(), folder, DAY)
    assert (folder / "2026-0001_Sender-GmbH.pdf").read_bytes() == b"Fritz's own file"
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_german_thousands_dot_is_read_as_thousands():
    from frits.billing import parse_amount

    assert parse_amount("1.400") == Decimal(1400)
    assert parse_amount("2.500.000") == Decimal(2_500_000)
    assert parse_amount("1.400,50") == Decimal("1400.50")
    assert parse_amount("40.50") == Decimal("40.50")
    assert parse_amount("0.3") == Decimal("0.3")


def test_paid_date_and_refund_after_storno(tmp_path):
    from frits.billing import list_invoices, mark_paid

    conn, number, _ = _issue(tmp_path, _settings())
    mark_paid(conn, number, date(2026, 11, 20))
    storno, pdf = cancel(conn, number, _settings(), tmp_path / "rechnungen", DAY)
    assert "erstatte ich" in " ".join(extract_text(pdf).text.split())
    mark_paid(conn, storno, date(2026, 11, 25))  # money went back
    status = {row["number"]: row["status"] for row in list_invoices(conn, DAY)}
    assert status == {number: "storniert", storno: "erstattet"}


def test_unpaid_storno_cannot_be_refunded_and_says_do_not_pay(tmp_path):
    from frits.billing import mark_paid

    conn, number, _ = _issue(tmp_path, _settings())
    storno, pdf = cancel(conn, number, _settings(), tmp_path / "rechnungen", DAY)
    assert "Bitte nicht zahlen" in " ".join(extract_text(pdf).text.split())
    with pytest.raises(BillingError, match="nie bezahlt"):
        mark_paid(conn, storno, DAY)


def test_pdf_names_keep_the_whole_customer_name(tmp_path):
    _, _, pdf = _issue(tmp_path, _settings())
    assert pdf.name == "2026-0001_Sender-GmbH.pdf"


def test_domestic_zero_rate_is_blocked():
    draft = _draft(
        Line("Vortrag", "2026-10-03", Decimal(1), "pauschal", Decimal(300), vat_rate=Decimal(0))
    )
    problems = blockers(draft, _settings(tax_mode="regelbesteuerung"))
    assert any("0 %" in p for p in problems)
