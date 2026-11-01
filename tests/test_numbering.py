from decimal import Decimal

from frits import store
from frits.billing import next_number, valid_pattern
from frits.settings import PriceItem, Settings


def _db(tmp_path, *numbers):
    conn = store.connect(tmp_path / "frits.db")
    for n in numbers:
        conn.execute(
            "INSERT INTO invoices (number, customer, issue_date, net, vat, gross)"
            " VALUES (?, 'x', '2026-01-01', '1', '0', '1')",
            (n,),
        )
    conn.commit()
    return conn


def test_default_pattern_counts_per_year(tmp_path):
    conn = _db(tmp_path, "2026-0001", "2026-0002", "2025-0099")
    assert next_number(conn, 2026) == "2026-0003"
    assert next_number(conn, 2027) == "2027-0001"


def test_own_pattern_and_last_number_before_frits(tmp_path):
    conn = _db(tmp_path)
    assert next_number(conn, 2026, "RE-JJ-NNN", "RE-26-041") == "RE-26-042"
    assert next_number(conn, 2027, "RE-JJ-NNN", "RE-26-041") == "RE-27-001"  # new year


def test_pattern_without_year_counts_on(tmp_path):
    conn = _db(tmp_path, "00007")
    assert next_number(conn, 2027, "NNNNN", "") == "00008"


def test_existing_invoices_win_over_an_older_last_number(tmp_path):
    conn = _db(tmp_path, "2026-0050")
    assert next_number(conn, 2026, "JJJJ-NNNN", "2026-0042") == "2026-0051"


def test_patterns_must_be_safe():
    assert valid_pattern("JJJJ-NNNN")
    assert valid_pattern("RE_JJ.NNN")
    assert not valid_pattern("JJJJ")  # no counter
    assert not valid_pattern("RE/NNN")  # a slash would break file names
    assert not valid_pattern("NN-NN")  # two counters


def test_price_list_matches_word_forms_and_unit():
    item = PriceItem("Fenster putzen", "Stk", Decimal(25))
    assert item.matches("3 Fenster geputzt", "Stk")
    assert not item.matches("3 Fenster geputzt", "Std")
    assert not item.matches("Rasen gemäht", "Stk")


def test_price_list_survives_saving(tmp_path):
    path = tmp_path / "settings.toml"
    Settings(prices=[{"name": "Hecke schneiden", "unit": "pauschal", "price": "60"}]).save(path)
    assert Settings.load(path).price_list == [PriceItem("Hecke schneiden", "pauschal", Decimal(60))]
    assert Settings(name="F", address="A", iban="I", prices=Settings.load(path).prices).is_complete
