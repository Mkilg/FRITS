import pytest
from conftest import DOCS, answer

from frits.customers import Customer
from frits.ingest import extract_text
from frits.redact import LeakError, Mapping, RedactedText, assert_no_leaks, redact

READABLE = [d for d in DOCS if not answer(d)["broken"]]
IMAGES = {".png", ".jpg", ".jpeg", ".heic"}


@pytest.mark.parametrize("doc", READABLE, ids=lambda p: p.name)
def test_every_planted_pii_value_is_removed(doc, nlp, customers, request):
    if doc.suffix in IMAGES:
        request.getfixturevalue("needs_ocr")
    expected = answer(doc)
    result = redact(extract_text(doc).text, customers, nlp)
    for value in expected["pii"]:
        assert value.lower() not in result.text.lower(), value
    for word in expected["contains"]:
        assert word in result.text, f"work content '{word}' was redacted"


def test_same_person_gets_same_placeholder(nlp, customers):
    r = redact("Erika Meier rief an. Frau Meier zahlt bar, sagt Erika.", customers, nlp)
    assert r.text.count("[PERSON_1]") == 3
    assert r.mapping.entries["[PERSON_1]"]["value"] == "Erika Meier"


def test_reidentify_restores_canonical_customer_name(nlp, customers):
    r = redact("Rechnung für Meier: 2 Std", customers, nlp)
    assert r.mapping.reidentify(r.text) == "Rechnung für Erika Meier: 2 Std"


def test_salutation_rule_catches_unknown_people(nlp):
    r = redact("Herr Wolf und Frau Dr. Lindqvist-Hansen kommen am Montag.", [], nlp)
    assert "Wolf" not in r.text and "Lindqvist" not in r.text
    assert "Montag" in r.text


def test_iban_split_over_two_lines(nlp):
    r = redact("IBAN DE89 3704 0044\n0532 0130 00\nDanke", [], nlp)
    assert "[IBAN_1]" in r.text and "0532" not in r.text and "Danke" in r.text


def test_invalid_iban_checksum_is_left_alone(nlp):
    assert "DE00 1234" in redact("Auftrag DE00 1234 5678 9012 3456 78", [], nlp).text


@pytest.mark.parametrize(
    "text",
    ["3 Std am 12.10.2026", "Summe 1.250,00 EUR", "Anfahrt 18 km", "Rechnung 2026-0001"],
)
def test_amounts_dates_and_units_survive(text, nlp):
    assert redact(text, [], nlp).text == text


def test_phone_numbers(nlp):
    r = redact("Ruf an: 0171 2345678 oder +49 (221) 98765-43", [], nlp)
    assert r.text == "Ruf an: [TEL_1] oder [TEL_2]"


def test_mapping_round_trip(tmp_path, nlp, customers):
    r = redact("Erika Meier, Gartenweg 3", customers, nlp)
    path = tmp_path / "m.json"
    r.mapping.save(path)
    assert Mapping.load(path).entries == r.mapping.entries


def test_leak_gate_blocks_known_values():
    mapping = Mapping({"[PERSON_1]": {"kind": "PERSON", "value": "Erika Meier", "surface": []}})
    with pytest.raises(LeakError):
        assert_no_leaks(RedactedText("Hallo Erika Meier", mapping))


def test_leak_gate_blocks_customers_and_patterns():
    c = Customer(id="x", name="Hanna Quell")
    with pytest.raises(LeakError):
        assert_no_leaks(RedactedText("von Quell", Mapping()), [c])
    with pytest.raises(LeakError):
        assert_no_leaks(RedactedText("mail an a@b.de", Mapping()))
