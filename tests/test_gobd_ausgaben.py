"""GoBD for expenses (strike, never delete) and sensible spacing between reminders."""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from frits import store
from frits.billing import BillingError
from frits.euer import summarize
from frits.expenses import (
    Expense,
    add_expense,
    delete_expense,
    expense_log,
    list_expenses,
    restore_expense,
)
from frits.reminders import REMINDER_DAYS, create_reminder
from frits.settings import Settings


def _expense(**changes) -> Expense:
    base = {
        "date": date(2026, 3, 4),
        "vendor": "Baumarkt",
        "description": "Schrauben",
        "gross": Decimal("11.90"),
        "vat_rate": Decimal("0.19"),
        "category": "waren",
    }
    return Expense(**(base | changes))


def test_deleting_strikes_the_expense_but_keeps_it(tmp_path):
    db = tmp_path / "frits.db"
    store.connect(db)
    kept = add_expense(db, _expense())
    gone = add_expense(db, _expense(vendor="Copyshop", gross=Decimal("100.00")))
    assert delete_expense(db, gone) is True
    assert [e.id for e in list_expenses(db)] == [kept]
    assert [e.id for e in list_expenses(db, 2026, struck=True)] == [gone]
    assert summarize(db, 2026, "kleinunternehmer").expense_total == Decimal("11.90")
    assert [action for _, action in expense_log(db, gone)] == ["gestrichen"]

    assert restore_expense(db, gone) is True
    assert restore_expense(db, gone) is False
    assert len(list_expenses(db)) == 2
    assert [action for _, action in expense_log(db, gone)] == ["gestrichen", "wiederhergestellt"]


def test_the_database_refuses_to_erase_or_rewrite(tmp_path):
    db = tmp_path / "frits.db"
    conn = store.connect(db)
    expense_id = add_expense(db, _expense())
    delete_expense(db, expense_id)
    with pytest.raises(sqlite3.IntegrityError, match="nie gelöscht"):
        conn.execute("DELETE FROM expenses")
    with pytest.raises(sqlite3.IntegrityError, match="bleiben, wie sie sind"):
        conn.execute("UPDATE expenses SET gross = '1.00'")
    with pytest.raises(sqlite3.IntegrityError, match="Protokoll"):
        conn.execute("DELETE FROM expense_log")


def test_old_databases_get_the_new_column(tmp_path):
    db = tmp_path / "frits.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,"
        " vendor TEXT NOT NULL, description TEXT NOT NULL, gross TEXT NOT NULL,"
        " vat_rate TEXT, category TEXT NOT NULL, source_file TEXT)"
    )
    old.execute(
        "INSERT INTO expenses (date, vendor, description, gross, vat_rate, category)"
        " VALUES ('2026-01-02', 'Alt', 'x', '5.00', NULL, 'buero')"
    )
    old.commit()
    old.close()
    store.connect(db)
    (expense,) = list_expenses(db)
    assert expense.vendor == "Alt"
    assert delete_expense(db, expense.id) and not list_expenses(db)


def _overdue_invoice(conn):
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, due_date, net, vat, gross)"
        " VALUES ('2026-0001', 'meier', '2026-09-01', '2026-09-15', '100', '0', '100')"
    )


def _settings() -> Settings:
    return Settings(
        name="Fritz", address="Musterweg 1, 10115 Berlin", iban="DE02120300000000202051"
    )


def test_next_reminder_waits_for_the_new_due_date(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    _overdue_invoice(conn)
    day = date(2026, 10, 1)
    _, first, new_due = create_reminder(conn, "2026-0001", _settings(), tmp_path, None, day)
    assert first.name == "2026-0001_Erinnerung_1.pdf"
    assert new_due == day + timedelta(days=REMINDER_DAYS)
    with pytest.raises(BillingError, match="läuft noch"):
        create_reminder(conn, "2026-0001", _settings(), tmp_path, None, day)
    with pytest.raises(BillingError, match="läuft noch"):
        create_reminder(conn, "2026-0001", _settings(), tmp_path, None, new_due)
    _, second, _ = create_reminder(
        conn, "2026-0001", _settings(), tmp_path, None, new_due + timedelta(days=1)
    )
    assert second.name == "2026-0001_Erinnerung_2.pdf"


def test_no_reminder_before_the_due_date(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    _overdue_invoice(conn)
    with pytest.raises(BillingError, match="noch nicht fällig"):
        create_reminder(conn, "2026-0001", _settings(), tmp_path, None, date(2026, 9, 15))


def test_an_invoice_without_draft_can_still_be_cancelled(tmp_path):
    from frits.billing import cancel

    conn = store.connect(tmp_path / "frits.db")
    _overdue_invoice(conn)
    settings = Settings(
        name="Fritz",
        address="Musterweg 1, 10115 Berlin",
        iban="DE02120300000000202051",
        steuernummer="12/345/67890",
    )
    storno, pdf = cancel(conn, "2026-0001", settings, tmp_path / "r", date(2026, 10, 1))
    row = conn.execute("SELECT net, gross FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert (row["net"], row["gross"]) == ("-100.00", "-100.00") and pdf.exists()


def test_receipt_words_suggest_a_category():
    from frits.beleg import guess_category

    assert guess_category("DB Fernverkehr AG\nFahrkarte Berlin-Köln") == "reisekosten"
    assert guess_category("Gaststätte Zur Linde\nBewirtung") == "bewirtung"
    assert guess_category("Deutsche Post\nPorto") == "buero"
    assert guess_category("Irgendwas") is None


def test_each_reminder_level_says_a_little_more(tmp_path):
    from frits.emails import reminder_email
    from frits.reminders import reminder_text

    issued, due = date(2026, 9, 1), date(2026, 10, 8)
    first = " ".join(reminder_text("2026-0001", issued, "100,00 €", 1, due, None))
    assert "untergegangen" in first and "08.10.2026" in first
    second = " ".join(reminder_text("2026-0001", issued, "100,00 €", 2, due, date(2026, 10, 1)))
    assert "am 01.10.2026" in second and "untergegangen" not in second
    third = " ".join(reminder_text("2026-0001", issued, "100,00 €", 3, due, date(2026, 10, 1)))
    assert "letzte freundliche" in third and "Raten" in third
    for text in (first, second, third):  # never fees or threats
        assert not any(word in text for word in ("Gebühr", "Zinsen", "Inkasso", "Gericht"))

    invoice = {"number": "2026-0001", "issue_date": "2026-09-01", "gross": "100"}
    subject, body = reminder_email(invoice, due, None, _settings(), 2, date(2026, 10, 1))
    assert subject == "2. Zahlungserinnerung zu Rechnung 2026-0001"
    assert "diese E-Mail erledigt" in body and "als PDF an" in body


def test_second_reminder_letter_names_the_first(tmp_path):
    from pypdf import PdfReader

    conn = store.connect(tmp_path / "frits.db")
    _overdue_invoice(conn)
    day = date(2026, 10, 1)
    _, _, new_due = create_reminder(conn, "2026-0001", _settings(), tmp_path, None, day)
    _, second, _ = create_reminder(
        conn, "2026-0001", _settings(), tmp_path, None, new_due + timedelta(days=1)
    )
    text = " ".join(page.extract_text() for page in PdfReader(second).pages)
    assert "2. Zahlungserinnerung" in text and "ich hatte Sie" in text


def test_no_fourth_reminder_after_the_last_friendly_one(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    _overdue_invoice(conn)
    day = date(2026, 10, 1)
    for _ in range(3):
        _, _, due = create_reminder(conn, "2026-0001", _settings(), tmp_path, None, day)
        day = due + timedelta(days=1)
    with pytest.raises(BillingError, match="Wie weiter"):
        create_reminder(conn, "2026-0001", _settings(), tmp_path, None, day)
