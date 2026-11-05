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
    # Lena (Runde 78b): last year over 25,000 € stops the invoice, like 100,000 € do.
    assert "2026 kein Kleinunternehmer" in limit_check(conn, klein, 2026)[0][0]
    _issued(conn, 2026, 75_000)
    assert len(limit_check(conn, klein, 2026, Decimal(2000))[0]) == 1  # one message, not two
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


def test_over_100k_says_one_thing_only(tmp_path):
    """Lena (Runde 74/75): over 100,000 € the draft also said "dieses Jahr bleibst du
    Kleinunternehmer". Now one message fits the case, and entering an earlier invoice names
    the invoice that crossed the line in the order they were written."""
    from frits.billing import earlier_limit_notes, limit_crossing

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _issued(conn, 2026, 99_000)
    problems, warnings = limit_check(conn, klein, 2026, Decimal(2000))
    assert "Mit dieser Rechnung kommst du" in problems[0] and warnings == []
    assert not any("100.000" in n for n in earlier_limit_notes(conn, klein, "2026-99000", DAY))
    # A later invoice (November) and then an earlier one entered afterwards (February):
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
        " VALUES ('2026-0001', 'x', '2026-11-05', '600', '0', '600')"
    )
    assert limit_crossing(conn, 2026) is None
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
        " VALUES ('A-18', 'x', '2026-02-01', '800', '0', '800')"
    )
    assert limit_crossing(conn, 2026) == ("Rechnung 2026-0001", date(2026, 11, 5))
    [note] = earlier_limit_notes(conn, klein, "A-18", DAY)
    assert "Rechnung 2026-0001 vom 05.11.2026" in note
    [problem] = limit_check(conn, klein, 2026, Decimal(10))[0]
    assert "seit der Rechnung 2026-0001 vom 05.11.2026 über 100.000" in problem
    assert earlier_limit_notes(conn, _settings(), "A-18", DAY) == []


def test_limit_texts_fit_the_case(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _issued(conn, 2026, 24_000)
    [w] = limit_check(conn, klein, 2026, Decimal(2000))[1]
    # No turnover from 2025 known: it may be the founding year, so no firm promise.
    assert w.startswith("Mit dieser Rechnung liegt") and "erst 2026 angefangen" in w
    assert "erinnert dich" not in w
    _issued(conn, 2025, 10_000)
    [w] = limit_check(conn, klein, 2026, Decimal(2000))[1]
    assert "Dieses Jahr bleibst du Kleinunternehmer" in w
    _issued(conn, 2026, 3_000)
    assert limit_check(conn, klein, 2026)[1][0].startswith("Dein Umsatz 2026 liegt")


def test_earlier_invoices_follow_the_rules_of_their_year(tmp_path):
    """Lena (Runde 75): 2024 had other limits without a switch during the year; crossing
    25,000 € in 2025 by entering an invoice means 2026 is no Kleinunternehmer year."""
    from frits.billing import earlier_limit_notes

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _issued(conn, 2024, 99_900)
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
        " VALUES ('A-1', 'x', '2024-03-01', '500', '0', '500')"
    )
    assert not any("100.000" in n for n in earlier_limit_notes(conn, klein, "A-1", DAY))
    _issued(conn, 2025, 24_900)
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
        " VALUES ('A-2', 'x', '2025-03-01', '500', '0', '500')"
    )
    [note] = earlier_limit_notes(conn, klein, "A-2", DAY)
    assert "2026 bist du dann kein Kleinunternehmer" in note


def test_a_draft_over_the_limit_is_not_ready(tmp_path):
    """Lena (Runde 75): Heute called a draft over 100,000 € "fertig", writing it then failed."""
    from frits.web.app import _open_drafts

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    new_draft(conn, _draft(_line("Moderation", "800")))
    [d] = _open_drafts(conn, klein, 2026)
    assert d["ready"] and not d["grenze"]
    _issued(conn, 2026, 99_500)
    [d] = _open_drafts(conn, klein, 2026)
    assert not d["ready"] and d["grenze"]


def _row(conn, number, day, net, cancels=None):
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross, cancels)"
        " VALUES (?, 'x', ?, ?, '0', ?, ?)",
        (number, day, str(net), str(net), cancels),
    )


def test_a_storno_counts_with_the_invoice_it_cancels(tmp_path):
    """Lena (Runde 78b): after cancelling A-18, entering A-19 took the year over 100,000 €
    without a word, because the Storno counted on its own later day."""
    from frits.billing import earlier_limit_notes, limit_crossing

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2026-0000", "2026-03-01", "99715.43")
    _row(conn, "A-18", "2026-02-01", "300")
    _row(conn, "2026-0001", "2026-11-05", "200")
    _row(conn, "2026-0002", "2026-11-05", "-300", cancels="A-18")
    assert limit_crossing(conn, 2026) is None  # 99,915.43 €, A-18 and its Storno cancel out
    _row(conn, "A-19", "2026-02-02", "200")
    [note] = earlier_limit_notes(conn, klein, "A-19", DAY)
    assert "Rechnung 2026-0001 vom 05.11.2026" in note and "berichtigt" in note


def test_same_day_invoices_count_in_number_order(tmp_path):
    from frits.billing import limit_crossing

    conn = store.connect(tmp_path / "frits.db")
    _row(conn, "2025-1", "2025-05-01", "99950")
    _row(conn, "2025-10", "2025-06-01", "30")
    _row(conn, "2025-9", "2025-06-01", "30")
    assert limit_crossing(conn, 2025)[0] == "Rechnung 2025-10"


def test_a_gutschrift_and_older_years_are_checked_too(tmp_path):
    from frits import gutschriften
    from frits.billing import limit_notes_after

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2026-0001", "2026-01-10", "99900")
    credit = gutschriften.add(
        conn,
        customer_id="sender",
        customer="Sender",
        their_number="GS-7",
        issue_date=date(2026, 3, 1),
        gross=Decimal(500),
        vat_rate=None,
    )
    [note] = limit_notes_after(
        conn, klein, f"c:{credit}", "Gutschrift GS-7", date(2026, 3, 1), Decimal(500), DAY
    )
    assert note.startswith("Mit dieser Gutschrift liegt dein Umsatz 2026 über 100.000 €.")
    # The customer wrote the Gutschrift, so the customer corrects it (Lena Runde 80).
    assert "Die Gutschriften ab dort muss dein Kunde berichtigen" in note
    [problem], _ = limit_check(conn, klein, 2026)
    assert "seit der Gutschrift GS-7" in problem
    assert "Gutschriften ab dort muss dein Kunde" in problem and "Rechnungen ab dort" not in problem
    # 2023 still had the old 22,000 € line for the next year.
    _row(conn, "2023-1", "2023-04-01", "21900")
    _row(conn, "A-3", "2023-05-01", "200")
    [note] = limit_notes_after(
        conn, klein, "r:A-3", "Rechnung A-3", date(2023, 5, 1), Decimal(200), DAY
    )
    assert "über 22.000 €" in note and "2024 warst du dann kein" in note


def test_entering_in_the_founding_year_says_what_it_means_now(tmp_path):
    from frits.billing import earlier_limit_notes

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2026-0001", "2026-03-01", "24900")
    _row(conn, "A-1", "2026-04-01", "200")
    [note] = earlier_limit_notes(conn, klein, "A-1", date(2026, 11, 5))
    assert "Damit liegt dein Umsatz 2026 über 25.000 €" in note
    assert "Hast du erst 2026 angefangen, gilt die Grenze schon jetzt" in note


def test_entering_last_year_names_the_invoices_to_correct(tmp_path):
    """Lena (Runde 80): an invoice entered for 2025 takes 2025 over 25,000 €. FRITS said
    "2026 bist du dann kein Kleinunternehmer" but not which 2026 invoices already went out
    without VAT and need correcting, and "check the settings" missed the point."""
    from frits.billing import earlier_limit_notes

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2025-1", "2025-02-01", "24900")
    _row(conn, "2026-10", "2026-03-01", "500")
    _row(conn, "2026-2", "2026-02-01", "1000")
    _row(conn, "2026-3", "2026-04-01", "300")
    _row(conn, "2026-4", "2026-05-01", "-300", cancels="2026-3")
    _row(conn, "2025-9", "2025-05-01", "200")
    [note] = earlier_limit_notes(conn, klein, "2025-9", date(2026, 11, 5))
    assert "Damit lag dein Umsatz 2025 über 25.000 €. 2026 bist du dann kein" in note
    assert (
        "Diese 2 Rechnungen von 2026 sind ohne Umsatzsteuer rausgegangen und müssen berichtigt "
        "werden: 2026-2, 2026-10." in note
    )
    assert "„Mit Umsatzsteuer“ um" in note and "Prüf bitte die Einstellungen" not in note
    # A year that is long over: only the correction, no switch in the settings.
    [old] = earlier_limit_notes(conn, klein, "2025-9", date(2027, 2, 1))
    assert "über die Berichtigung" in old and "„Mit Umsatzsteuer“ um" not in old


def test_a_storno_back_under_100k_says_so(tmp_path):
    from frits.billing import limit_crossing, storno_limit_note

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2026-1", "2026-01-10", "99900")
    _row(conn, "2026-2", "2026-03-01", "500")
    before = limit_crossing(conn, 2026)
    assert before == ("Rechnung 2026-2", date(2026, 3, 1))
    _row(conn, "2026-3", "2026-11-05", "-500", cancels="2026-2")
    [note] = storno_limit_note(conn, klein, 2026, before)
    assert note.startswith("Mit dem Storno liegt dein Umsatz 2026 wieder unter 100.000 €.")
    assert "Vorher lag er ab der Rechnung 2026-2 vom 01.03.2026 darüber" in note
    assert limit_crossing(conn, 2026) is None  # the cancelled invoice crosses nothing now
    assert storno_limit_note(conn, _settings(), 2026, before) == []
    assert storno_limit_note(conn, klein, 2026, None) == []


def test_last_year_straight_over_100k_and_entries_after_the_line(tmp_path):
    """Lena (Runde 82): a Vorjahr that jumps over 100,000 € at once also lists this year's
    invoices and Gutschriften to correct; and once a year is over the line, a Gutschrift or
    an invoice entered after it says that it needs VAT as well."""
    from frits import gutschriften
    from frits.billing import earlier_limit_notes, limit_notes_after

    conn = store.connect(tmp_path / "frits.db")
    klein = _settings(tax_mode="kleinunternehmer")
    _row(conn, "2026-0003", "2026-03-01", "97500")
    gutschriften.add(
        conn,
        customer_id="sender",
        customer="Sender",
        their_number="GS-1",
        issue_date=date(2026, 2, 1),
        gross=Decimal(800),
        vat_rate=None,
    )
    _row(conn, "2025-1", "2025-04-01", "120000")
    [note] = earlier_limit_notes(conn, klein, "2025-1", date(2026, 11, 5))
    assert "Mit dieser Rechnung lag dein Umsatz 2025 über 100.000 €" in note
    assert "Diese Rechnung von 2026 ist ohne Umsatzsteuer rausgegangen" in note
    assert "2026-0003" in note and "(GS-1) berichtigt dein Kunde" in note
    # 2026 goes over the line with A-1; a Gutschrift after it needs VAT too.
    _row(conn, "A-1", "2026-04-01", "3000")
    credit = gutschriften.add(
        conn,
        customer_id="sender",
        customer="Sender",
        their_number="GS-2",
        issue_date=date(2026, 5, 1),
        gross=Decimal(100),
        vat_rate=None,
    )
    [note] = limit_notes_after(
        conn, klein, f"c:{credit}", "Gutschrift GS-2", date(2026, 5, 1), Decimal(100), DAY
    )
    assert note.startswith("Dein Umsatz 2026 liegt schon seit der Rechnung A-1 vom 01.04.2026")
    assert "Bitte deinen Kunden um eine berichtigte Gutschrift" in note
    # An entry before the line changes nothing about the line: no note of that kind.
    _row(conn, "A-0", "2026-01-05", "10")
    notes = limit_notes_after(
        conn, klein, "r:A-0", "Rechnung A-0", date(2026, 1, 5), Decimal(10), DAY
    )
    assert not any("schon seit" in n for n in notes)
