"""Reading an old invoice (written before FRITS) on this computer, without AI."""

from datetime import date

from frits.billing import EARLIER_TAX
from frits.frueher import lesen

TODAY = date(2026, 11, 2)
OWN = ("Fritz Beispiel", "", "Werkstattweg 1, 12345 Musterstadt")

KLEINUNTERNEHMER = """Fritz Beispiel · Werkstattweg 1 · 12345 Musterstadt
Radio Süd GmbH
Frau Erika Meier
Hauptstraße 5
80331 München

Musterstadt, 12. März 2025

Rechnung Nr. 17

Leistungsdatum: 10.03.2025

Pos. Beschreibung Menge Einzelpreis Gesamt
1 Moderation Workshop 1 pauschal 1.200,00 € 1.200,00 €

Gesamtbetrag 1.200,00 €
Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.
Bitte überweisen Sie den Betrag bis zum 26.03.2025.
Fritz Beispiel · IBAN DE89 3704 0044 0532 0130 00 · Steuernummer 123/456/78901
"""

NEUNZEHN = """Fritz Beispiel
Werkstattweg 1
12345 Musterstadt
Tel. 0123 456789
fritz@example.com

Studio Nord GmbH
z. Hd. Herrn Max Mustermann
Am Hafen 12
20457 Hamburg

Rechnungsnummer: 2025-014
Rechnungsdatum: 03.04.2025
Leistungszeitraum: 01.03.2025 – 31.03.2025

Leistung Stunden Satz Betrag
Schnitt Imagefilm 10 Std 80,00 € 800,00 €
Nettobetrag 800,00 €
zzgl. 19 % USt 152,00 €
Gesamtbetrag 952,00 €
Zahlbar bis 17.04.2025 ohne Abzug.
"""

SIEBEN = """Fritz Beispiel, Werkstattweg 1, 12345 Musterstadt
Buchhandlung Seitenweise
Lindenallee 3
50667 Köln
RE-2024-07
Datum: 15.11.2024
Leistung: Lektorat Kinderbuch
Rechnungsbetrag 535,00 EUR
enthaltene MwSt 7 %: 35,00 EUR
"""

NUR_BRUTTO = """Rechnung
An: Café Krümel, Marktplatz 2, 01067 Dresden
Rechnungs-Nr.: 88
Datum 2. Januar 2026
Fotos Hochzeit
Summe brutto: 1.190,00 €
inkl. 19 % MwSt.
"""


def test_kleinunternehmer_invoice_is_read():
    found = lesen(KLEINUNTERNEHMER, OWN, TODAY)
    assert found == {
        "number": "17",
        "customer": "Radio Süd GmbH",
        "issue_date": "2025-03-12",
        "service_date": "2025-03-10",
        "due_date": "2025-03-26",
        "description": "Moderation Workshop",
        "net": "1.200,00",
        "tax": "klein",
    }


def test_invoice_with_19_percent_takes_the_net_and_the_end_of_the_period():
    found = lesen(NEUNZEHN, OWN, TODAY)
    assert found["number"] == "2025-014"
    assert found["customer"] == "Studio Nord GmbH"  # not Fritz's own letterhead above it
    assert found["issue_date"] == "2025-04-03"
    assert found["service_date"] == "2025-03-31"
    assert found["description"] == "Schnitt Imagefilm"
    assert found["net"] == "800,00" and found["tax"] == "19"


def test_letterhead_is_recognised_even_without_fritzs_name():
    # Phone and mail mark the block at the top as the sender's.
    assert lesen(NEUNZEHN, None, TODAY)["customer"] == "Studio Nord GmbH"


def test_invoice_with_7_percent_gets_the_net_from_gross_minus_vat():
    found = lesen(SIEBEN, "Fritz Beispiel", TODAY)
    assert found["number"] == "RE-2024-07"
    assert found["customer"] == "Buchhandlung Seitenweise"
    assert found["issue_date"] == "2024-11-15"
    assert found["description"] == "Lektorat Kinderbuch"
    assert found["net"] == "500,00" and found["tax"] == "7"


def test_gross_only_is_turned_into_net():
    found = lesen(NUR_BRUTTO, OWN, TODAY)
    assert found["number"] == "88"
    assert found["customer"] == "Café Krümel"
    assert found["issue_date"] == "2026-01-02"
    assert found["net"] == "1.000,00" and found["tax"] == "19"


def test_gross_with_odd_cents_is_rounded_to_cents():
    text = "Rechnung Nr. 5\nDatum: 01.02.2026\nGesamtbetrag: 100,00 €\nenthält 19 % USt\n"
    assert lesen(text, OWN, TODAY)["net"] == "84,03"


def test_every_tax_guess_is_a_choice_of_the_form():
    for text in (KLEINUNTERNEHMER, NEUNZEHN, SIEBEN, NUR_BRUTTO):
        assert lesen(text, OWN, TODAY)["tax"] in EARLIER_TAX


def test_unsure_things_stay_empty():
    # Two rates on one invoice, or reverse charge: Fritz chooses himself.
    mixed = "Rechnung Nr. 9\nNetto 100,00 €\n19 % USt 10,00 €\n7 % USt 2,00 €\n"
    assert "tax" not in lesen(mixed, OWN, TODAY)
    reverse = "Invoice No. 12\nNetto 500,00 €\nSteuerschuldnerschaft des Leistungsempfängers\n19 %"
    found = lesen(reverse, OWN, TODAY)
    assert "tax" not in found and found["net"] == "500,00" and found["number"] == "12"
    # Only a total, no word about VAT: the net amount can't be known.
    assert "net" not in lesen("Rechnung Nr. 3\nGesamtbetrag 300,00 €\n", OWN, TODAY)
    assert lesen("Hallo, wie geht's?", OWN, TODAY) == {}


def test_own_details_are_never_the_customer():
    text = "Fritz Beispiel\nWerkstattweg 1\n12345 Musterstadt\n\nRechnung Nr. 4\n"
    assert "customer" not in lesen(text, OWN, TODAY)


def test_other_numbers_are_not_the_invoice_number():
    text = "Kundennummer: 4711\nIhre Nr.: 0815\nRechnungsnummer: 2026-0003\n"
    assert lesen(text, OWN, TODAY)["number"] == "2026-0003"
    assert "number" not in lesen("Kundennummer: 4711\nIhre Nr.: 0815\n", OWN, TODAY)


def test_label_above_its_value_and_future_dates():
    text = "Rechnungsnummer\n2026/12\nRechnungsdatum:\n03.05.2026\nFällig am 03.12.2026\n"
    found = lesen(text, OWN, TODAY)
    assert found["number"] == "2026/12"
    assert found["issue_date"] == "2026-05-03"
    assert "service_date" not in found


def test_same_service_and_issue_day_leaves_the_service_date_empty():
    text = "Rechnung Nr. 8\nRechnungsdatum: 03.05.2026\nLeistungsdatum: 03.05.2026\n"
    assert "service_date" not in lesen(text, OWN, TODAY)
