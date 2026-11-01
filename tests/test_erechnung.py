"""E-Rechnung (ZUGFeRD / Factur-X, EN 16931) inside every invoice PDF."""

from datetime import date
from decimal import Decimal

from facturx import get_xml_from_pdf, xml_check_xsd
from lxml import etree
from pypdf import PdfReader

from frits import store
from frits.billing import Draft, Line, approve, cancel, new_draft
from frits.erechnung import NS, split_address
from frits.settings import Settings

DAY = date(2026, 11, 2)


def _settings(**changes) -> Settings:
    base = {
        "name": "Fritz Beispiel",
        "address": "Musterweg 1, 10115 Berlin",
        "email": "fritz@example.org",
        "iban": "DE02 1203 0000 0000 2020 51",
        "hourly_rate": Decimal(80),
        "steuernummer": "12/345/67890",
    }
    return Settings(**(base | changes))


def _draft(*lines, **customer) -> Draft:
    return Draft(
        customer_id="kunde",
        customer_name=customer.pop("name", "Sender GmbH"),
        customer_address=customer.pop("address", "Funkstraße 2, 80331 München"),
        lines=list(lines)
        or [Line("Moderation Podium", "2026-10-03", Decimal(2), "Std", Decimal(80))],
        questions=[],
        warnings=[],
        source_file="notiz.txt",
        **customer,
    )


def _xml(tmp_path, settings, draft):
    conn = store.connect(tmp_path / "frits.db")
    number, pdf = approve(conn, new_draft(conn, draft), settings, tmp_path / "r", DAY)
    name, xml = get_xml_from_pdf(pdf.read_bytes(), check_xsd=False)
    assert name == "factur-x.xml"
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931")
    return conn, number, pdf, etree.fromstring(xml)


def _all(tree, path):
    return [node.text for node in tree.iterfind(path, NS)]


def _one(tree, path):
    found = _all(tree, path)
    assert len(found) == 1, (path, found)
    return found[0]


def test_kleinunternehmer_invoice_carries_valid_xml(tmp_path):
    _, number, pdf, tree = _xml(tmp_path, _settings(), _draft())
    assert _one(tree, ".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID").endswith("2017")
    assert _one(tree, "rsm:ExchangedDocument/ram:ID") == number
    assert _one(tree, ".//ram:IssueDateTime/udt:DateTimeString") == "20261102"
    assert _all(
        tree, ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode"
    ) == ["E"]
    assert "§ 19" in _one(tree, ".//ram:ExemptionReason")
    assert _one(tree, ".//ram:GrandTotalAmount") == "160.00"
    assert _one(tree, ".//ram:BilledQuantity") == "2"
    assert tree.find(".//ram:BilledQuantity", NS).get("unitCode") == "HUR"
    assert _one(tree, ".//ram:IBANID") == "DE02120300000000202051"
    assert _one(tree, ".//ram:BuyerTradeParty/ram:PostalTradeAddress/ram:CityName") == "München"
    assert _one(tree, ".//ram:OccurrenceDateTime/udt:DateTimeString") == "20261003"
    # PDF/A-3 with the Factur-X description, so readers find the XML
    root = PdfReader(pdf).trailer["/Root"]
    xmp = root["/Metadata"].get_object().get_data()
    assert b"pdfaid:part>3<" in xmp or b'pdfaid:part="3"' in xmp
    assert b"factur-x.xml" in xmp and b"EN 16931" in xmp
    assert "/OutputIntents" in root and "/AF" in root


def test_mixed_rates_and_sums_add_up(tmp_path):
    draft = _draft(
        Line("Moderation", "2026-10-03", Decimal(1), "pauschal", Decimal(500)),
        Line(
            "Artikel mit Nutzungsrechten",
            "2026-10-07",
            Decimal(1),
            "pauschal",
            Decimal(300),
            vat_rate=Decimal(7),
        ),
    )
    settings = _settings(tax_mode="regelbesteuerung", ust_id="DE123456789")
    _, _, _, tree = _xml(tmp_path, settings, draft)
    head = ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/"
    assert _all(tree, head + "ram:CategoryCode") == ["S", "S"]
    assert _all(tree, head + "ram:RateApplicablePercent") == ["19", "7"]
    assert _all(tree, head + "ram:CalculatedAmount") == ["95.00", "21.00"]
    assert _one(tree, ".//ram:TaxTotalAmount") == "116.00"
    assert _one(tree, ".//ram:GrandTotalAmount") == "916.00"
    assert _all(tree, ".//ram:BillingSpecifiedPeriod//udt:DateTimeString") == [
        "20261003",
        "20261007",
    ]
    assert _all(tree, ".//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID") == [
        "DE123456789",
        "12/345/67890",
    ]


def test_reverse_charge_and_outside_the_eu(tmp_path):
    settings = _settings(tax_mode="regelbesteuerung", ust_id="DE123456789")
    eu = _draft(
        customer_tax="eu",
        customer_ust_id="ATU12345678",
        customer_country="AT",
        address="Funkgasse 3, 1010 Wien",
    )
    _, _, _, tree = _xml(tmp_path / "eu", settings, eu)
    assert _one(tree, ".//ram:ApplicableHeaderTradeSettlement//ram:CategoryCode") == "AE"
    assert _one(tree, ".//ram:BuyerTradeParty//ram:CountryID") == "AT"
    assert _one(tree, ".//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID") == "ATU12345678"

    ch = _draft(
        customer_tax="drittland",
        customer_country="CH",
        customer_reference="PO-4711",
        address="Funkweg 2, 8000 Zürich",
    )
    _, _, _, tree = _xml(tmp_path / "ch", settings, ch)
    assert _one(tree, ".//ram:ApplicableHeaderTradeSettlement//ram:CategoryCode") == "O"
    assert _one(tree, ".//ram:BuyerReference") == "PO-4711"
    # BR-O-02: no VAT IDs with category O, only the Steuernummer
    assert _all(tree, ".//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID") == [
        "12/345/67890"
    ]


def test_storno_points_to_the_original(tmp_path):
    conn, number, _, _ = _xml(tmp_path, _settings(), _draft())
    _, pdf = cancel(conn, number, _settings(), tmp_path / "r", DAY)
    _, xml = get_xml_from_pdf(pdf.read_bytes(), check_xsd=False)
    tree = etree.fromstring(xml)
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931")
    assert _one(tree, ".//ram:InvoiceReferencedDocument/ram:IssuerAssignedID") == number
    assert _one(tree, ".//ram:GrandTotalAmount") == "-160.00"


def test_xml_trouble_never_blocks_an_invoice(tmp_path, monkeypatch):
    import frits.erechnung

    def broken(*args, **kwargs):
        raise ValueError("kaputt")

    monkeypatch.setattr(frits.erechnung, "embed", broken)
    conn = store.connect(tmp_path / "frits.db")
    _, pdf = approve(conn, new_draft(conn, _draft()), _settings(), tmp_path / "r", DAY)
    assert pdf.read_bytes().startswith(b"%PDF") and b"factur-x.xml" not in pdf.read_bytes()


def test_addresses_split_into_street_postcode_city():
    assert split_address("Musterweg 1, 10115 Berlin") == ("Musterweg 1", "10115", "Berlin")
    assert split_address("c/o Studio, Funkweg 2, 8000 Zürich") == (
        "c/o Studio, Funkweg 2",
        "8000",
        "Zürich",
    )
    assert split_address("Irgendwo") == ("Irgendwo", "", "Irgendwo")
