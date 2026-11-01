import csv
import sqlite3
from datetime import date
from decimal import Decimal as D

import pytest

from frits.euer import (
    LINE_KLEINUNTERNEHMER,
    LINE_NET_INCOME,
    LINE_TAX_FREE,
    LINE_VAT_COLLECTED,
    LINE_VORSTEUER,
    summarize,
    write_csv,
    write_pdf,
)
from frits.expenses import (
    CATEGORIES,
    Expense,
    add_expense,
    delete_expense,
    init_db,
    list_expenses,
)

# Matches the invoices table owned by the core build. Money is Decimal stored as text.
INVOICES_SCHEMA = """
CREATE TABLE invoices (
    number TEXT PRIMARY KEY,
    customer TEXT,
    issue_date TEXT,
    paid_date TEXT NULL,
    net TEXT,
    vat TEXT,
    gross TEXT
)
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "frits.sqlite"
    init_db(path)
    conn = sqlite3.connect(path)
    conn.execute(INVOICES_SCHEMA)
    conn.executemany(
        "INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-001", "Kunde A", "2026-01-10", "2026-01-20", "1000.00", "190.00", "1190.00"),
            ("2026-002", "Kunde B", "2026-03-01", "2026-03-15", "500.00", "95.00", "595.00"),
            # Unpaid: does not count yet.
            ("2026-003", "Kunde C", "2026-11-01", None, "300.00", "57.00", "357.00"),
            # Written in 2025, paid in 2026: counts in 2026.
            ("2025-009", "Kunde D", "2025-12-20", "2026-01-05", "100.00", "19.00", "119.00"),
            # Paid in 2025: not in 2026.
            ("2025-008", "Kunde E", "2025-11-01", "2025-11-30", "200.00", "38.00", "238.00"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def exp(**kw):
    base = {
        "date": date(2026, 2, 1),
        "vendor": "Laden",
        "description": "Kram",
        "gross": D("119.00"),
        "vat_rate": D("0.19"),
        "category": "buero",
    }
    base.update(kw)
    return Expense(**base)


def test_net_and_vat_are_computed_from_gross():
    e = exp(gross=D("119.00"), vat_rate=D("0.19"))
    assert (e.net, e.vat) == (D("100.00"), D("19.00"))
    e = exp(gross=D("10.70"), vat_rate=D("0.07"))
    assert (e.net, e.vat) == (D("10.00"), D("0.70"))
    e = exp(gross=D("9.99"), vat_rate=None)
    assert (e.net, e.vat) == (D("9.99"), D(0))


def test_rounding_to_cents():
    e = exp(gross=D("1.00"), vat_rate=D("0.19"))
    assert e.net == D("0.84")
    assert e.net + e.vat == D("1.00")


@pytest.mark.parametrize(
    "kw",
    [
        {"gross": 12.5},
        {"gross": D(-1)},
        {"vat_rate": D("0.16")},
        {"category": "luxusyacht"},
    ],
)
def test_invalid_expenses_are_rejected(kw):
    with pytest.raises((TypeError, ValueError)):
        exp(**kw)


def test_every_category_has_an_euer_line():
    for cat in CATEGORIES.values():
        assert cat.label and cat.euer_line
        assert D(0) < cat.deductible_share <= D(1)


def test_store_list_delete_roundtrip(tmp_path):
    path = tmp_path / "x.sqlite"
    init_db(path)
    init_db(path)  # calling twice is harmless
    a = add_expense(path, exp(date=date(2026, 5, 1), source_file="inbox/bon.jpg"))
    add_expense(path, exp(date=date(2026, 1, 1), vat_rate=None, gross=D("4.20")))
    add_expense(path, exp(date=date(2025, 12, 31)))

    all_rows = list_expenses(path)
    assert [e.date for e in all_rows] == [date(2025, 12, 31), date(2026, 1, 1), date(2026, 5, 1)]
    y2026 = list_expenses(path, year=2026)
    assert len(y2026) == 2
    assert y2026[0].vat_rate is None and y2026[0].gross == D("4.20")
    assert y2026[1].source_file == "inbox/bon.jpg"
    assert isinstance(y2026[1].gross, D)

    assert delete_expense(path, a) is True
    assert delete_expense(path, a) is False
    assert len(list_expenses(path, year=2026)) == 1


def test_euer_kleinunternehmer_counts_gross(db):
    add_expense(db, exp(gross=D("119.00"), category="buero"))
    add_expense(db, exp(gross=D("50.00"), category="software"))  # same line as buero
    add_expense(db, exp(gross=D("100.00"), category="bewirtung"))  # only 70 % count
    add_expense(db, exp(date=date(2025, 6, 1), gross=D("999.00")))  # other year

    s = summarize(db, 2026, "kleinunternehmer")
    assert s.invoice_count == 3 and s.expense_count == 3
    assert [(line.label, line.amount) for line in s.income_lines] == [
        (LINE_KLEINUNTERNEHMER, D("1904.00"))  # 1190 + 595 + 119
    ]
    costs = {line.label: line.amount for line in s.expense_lines}
    assert costs == {
        "Bewirtungsaufwendungen": D("70.00"),
        "Übrige unbeschränkt abziehbare Betriebsausgaben": D("169.00"),
    }
    assert s.expense_total == D("239.00")
    assert s.profit == D("1665.00")


def test_euer_regelbesteuerung_splits_vat(db):
    add_expense(db, exp(gross=D("119.00"), vat_rate=D("0.19"), category="telekommunikation"))
    add_expense(db, exp(gross=D("119.00"), vat_rate=D("0.19"), category="bewirtung"))
    add_expense(db, exp(gross=D("20.00"), vat_rate=None, category="fortbildung"))

    s = summarize(db, 2026, "regelbesteuerung")
    income = {line.label: line.amount for line in s.income_lines}
    assert income == {LINE_NET_INCOME: D("1600.00"), LINE_VAT_COLLECTED: D("304.00")}
    costs = {line.label: line.amount for line in s.expense_lines}
    assert costs == {
        "Aufwendungen für Telekommunikation": D("100.00"),
        "Bewirtungsaufwendungen": D("70.00"),
        "Fortbildungskosten": D("20.00"),
        LINE_VORSTEUER: D("38.00"),  # VAT on meals is fully reclaimable
    }
    assert s.profit == D("1904.00") - D("228.00")


def test_euer_puts_income_without_german_vat_on_its_own_line(db):
    conn = sqlite3.connect(db)
    # Reverse Charge for a broadcaster in Austria, paid in 2026
    conn.execute(
        "INSERT INTO invoices VALUES ('2026-004', 'ORF', '2026-02-01', '2026-02-10',"
        " '800.00', '0.00', '800.00')"
    )
    conn.commit()
    conn.close()
    income = {
        line.label: line.amount for line in summarize(db, 2026, "regelbesteuerung").income_lines
    }
    assert income == {
        LINE_NET_INCOME: D("1600.00"),
        LINE_VAT_COLLECTED: D("304.00"),
        LINE_TAX_FREE: D("800.00"),
    }


def test_euer_without_invoices_table(tmp_path):
    path = tmp_path / "only-expenses.sqlite"
    init_db(path)
    add_expense(path, exp())
    s = summarize(path, 2026, "kleinunternehmer")
    assert s.income_total == 0
    assert s.profit == D("-119.00")


def test_unknown_tax_mode(db):
    with pytest.raises(ValueError):
        summarize(db, 2026, "vielleicht")


def test_csv_export(db, tmp_path):
    add_expense(db, exp(gross=D("1234.56"), vat_rate=None))
    out = write_csv(summarize(db, 2026, "kleinunternehmer"), tmp_path / "euer.csv")
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0] == ["Art", "Zeile Anlage EÜR", "Betrag (EUR)"]
    assert ["Einnahme", LINE_KLEINUNTERNEHMER, "1.904,00"] in rows
    assert rows[-1] == ["Ergebnis", "Gewinn/Verlust", "669,44"]


def test_pdf_export(db, tmp_path):
    add_expense(db, exp())
    out = write_pdf(summarize(db, 2026, "regelbesteuerung"), tmp_path / "euer.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_pdf_export_loss_and_empty(tmp_path):
    path = tmp_path / "empty.sqlite"
    init_db(path)
    add_expense(path, exp())
    out = write_pdf(summarize(path, 2026, "kleinunternehmer"), tmp_path / "loss.pdf")
    assert out.read_bytes().startswith(b"%PDF")
