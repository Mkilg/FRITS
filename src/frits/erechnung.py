"""E-Rechnung: the invoice as structured data (EN 16931, ZUGFeRD / Factur-X profile EN 16931).

The XML travels inside the invoice PDF, so one file works for people and for accounting
software. German businesses must be able to receive such invoices since 2025; issuing them
becomes mandatory for B2B invoices from 2027 (turnover over 800,000 €) or 2028 (everyone).
Kleinunternehmer stay exempt (§ 34a UStDV), but a valid e-invoice never hurts.

Everything here is computed from the same Draft and Totals as the PDF, so both always agree.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from lxml import etree

from frits.billing import Draft, Totals, money, service_dates
from frits.settings import Settings

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
}
PROFILE = "urn:cen.eu:en16931:2017"
# UN/ECE Recommendation 20 unit codes for FRITS's units.
UNIT_CODES = {"Std": "HUR", "km": "KMT", "Stk": "H87", "pauschal": "LS"}
EXEMPTION = {
    "E": "Kleinunternehmer gemäß § 19 UStG",
    "AE": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)",
    "O": "Nicht im Inland steuerbare Leistung",
}
_POSTCODE_CITY = re.compile(r"^(?:[A-Z]{1,2}-)?(\d{4,5})\s+(.+)$")


def _el(parent, tag: str, text: str | None = None, **attrs) -> etree._Element:
    prefix, name = tag.split(":")
    node = etree.SubElement(parent, f"{{{NS[prefix]}}}{name}", **attrs)
    if text is not None:
        node.text = text
    return node


def _amount(value: Decimal) -> str:
    return f"{money(value):.2f}"


def _date(parent, tag: str, day: date) -> None:
    holder = _el(parent, tag)
    _el(holder, "udt:DateTimeString", day.strftime("%Y%m%d"), format="102")


def split_address(address: str | None) -> tuple[str, str, str]:
    """ "Weg 1, 12345 Ort" -> ("Weg 1", "12345", "Ort"). Unknown shapes keep the text whole."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    for index in range(len(parts) - 1, -1, -1):
        if match := _POSTCODE_CITY.match(parts[index]):
            street = ", ".join(parts[:index]) or parts[index]
            return street, match.group(1), match.group(2)
    return ", ".join(parts), "", parts[-1] if parts else ""


def _category(sums: Totals, rate: Decimal, customer_tax: str) -> str:
    if sums.vat_rate is None:
        return "E"
    if customer_tax == "eu":
        return "AE"
    if customer_tax == "drittland":
        return "O"
    return "S" if rate > 0 else "Z"


def _party(
    parent, tag: str, name: str, address: str | None, country: str, tax_ids, email=None, ident=None
):
    party = _el(parent, tag)
    if ident:  # BT-29: lets the buyer identify the seller when no VAT ID may be given
        _el(party, "ram:ID", ident)
    _el(party, "ram:Name", name)
    street, postcode, city = split_address(address)
    postal = _el(party, "ram:PostalTradeAddress")
    if postcode:
        _el(postal, "ram:PostcodeCode", postcode)
    if street:
        _el(postal, "ram:LineOne", street)
    if city:
        _el(postal, "ram:CityName", city)
    _el(postal, "ram:CountryID", country)
    if email:
        uri = _el(party, "ram:URIUniversalCommunication")
        _el(uri, "ram:URIID", email, schemeID="EM")
    for scheme, value in tax_ids:
        registration = _el(party, "ram:SpecifiedTaxRegistration")
        _el(registration, "ram:ID", value, schemeID=scheme)
    return party


def invoice_xml(
    number: str,
    draft: Draft,
    sums: Totals,
    settings: Settings,
    issued: date,
    due: date | None,
    cancels: str | None = None,
    cancels_date: date | None = None,
) -> bytes:
    """The EN 16931 XML for one invoice or Storno."""
    customer_tax = (draft.tax or {}).get("customer", draft.customer_tax) or ""
    root = etree.Element(f"{{{NS['rsm']}}}CrossIndustryInvoice", nsmap=NS)
    context = _el(root, "rsm:ExchangedDocumentContext")
    _el(_el(context, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", PROFILE)

    document = _el(root, "rsm:ExchangedDocument")
    _el(document, "ram:ID", number)
    _el(document, "ram:TypeCode", "380")
    _date(document, "ram:IssueDateTime", issued)
    if cancels:
        note = _el(document, "ram:IncludedNote")
        _el(note, "ram:Content", f"Stornorechnung zu {cancels}")

    trade = _el(root, "rsm:SupplyChainTradeTransaction")
    standard = sums.vat_rate if sums.vat_rate is not None else Decimal(0)
    for index, line in enumerate(draft.lines, start=1):
        if line.net is None or line.unit_price is None:
            continue
        item = _el(trade, "ram:IncludedSupplyChainTradeLineItem")
        _el(_el(item, "ram:AssociatedDocumentLineDocument"), "ram:LineID", str(index))
        _el(_el(item, "ram:SpecifiedTradeProduct"), "ram:Name", line.description)
        agreement = _el(item, "ram:SpecifiedLineTradeAgreement")
        price = _el(agreement, "ram:NetPriceProductTradePrice")
        _el(price, "ram:ChargeAmount", f"{line.unit_price.normalize():f}")
        delivery = _el(item, "ram:SpecifiedLineTradeDelivery")
        unit = UNIT_CODES.get(line.unit, "C62")
        _el(delivery, "ram:BilledQuantity", f"{line.quantity.normalize():f}", unitCode=unit)
        settlement = _el(item, "ram:SpecifiedLineTradeSettlement")
        rate = _line_rate(sums, line.vat_rate, standard, customer_tax)
        tax = _el(settlement, "ram:ApplicableTradeTax")
        _el(tax, "ram:TypeCode", "VAT")
        category = _category(sums, rate, customer_tax)
        _el(tax, "ram:CategoryCode", category)
        if category != "O":  # BR-O-05: no rate for "not subject to VAT"
            _el(tax, "ram:RateApplicablePercent", f"{rate.normalize():f}")
        total = _el(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation")
        _el(total, "ram:LineTotalAmount", _amount(line.net))

    agreement = _el(trade, "ram:ApplicableHeaderTradeAgreement")
    _el(agreement, "ram:BuyerReference", draft.customer_reference or number)
    seller_ids = []
    if settings.ust_id.strip() and customer_tax != "drittland":
        seller_ids.append(("VA", settings.ust_id.strip()))
    if settings.steuernummer.strip():
        seller_ids.append(("FC", settings.steuernummer.strip()))
    _party(
        agreement,
        "ram:SellerTradeParty",
        settings.company or settings.name,
        settings.address,
        "DE",
        seller_ids,
        settings.email or None,
        ident=settings.steuernummer.strip() or settings.ust_id.strip() or None,
    )
    buyer_ids = []
    if customer_tax == "eu" and draft.customer_ust_id:
        buyer_ids.append(("VA", draft.customer_ust_id))
    _party(
        agreement,
        "ram:BuyerTradeParty",
        draft.customer_name or "",
        draft.customer_address,
        draft.customer_country or "DE",
        buyer_ids,
    )

    delivery = _el(trade, "ram:ApplicableHeaderTradeDelivery")
    days = service_dates(draft.lines)
    if days and days[0] == days[-1]:
        event = _el(delivery, "ram:ActualDeliverySupplyChainEvent")
        _date(event, "ram:OccurrenceDateTime", days[0])
    elif not days:
        event = _el(delivery, "ram:ActualDeliverySupplyChainEvent")
        _date(event, "ram:OccurrenceDateTime", issued)

    settlement = _el(trade, "ram:ApplicableHeaderTradeSettlement")
    _el(settlement, "ram:PaymentReference", cancels or number)
    _el(settlement, "ram:InvoiceCurrencyCode", "EUR")
    if settings.iban and not cancels:
        means = _el(settlement, "ram:SpecifiedTradeSettlementPaymentMeans")
        _el(means, "ram:TypeCode", "58")
        account = _el(means, "ram:PayeePartyCreditorFinancialAccount")
        _el(account, "ram:IBANID", settings.iban.replace(" ", ""))
    for rate, part, vat in sums.groups or ((Decimal(0), sums.net, sums.vat),):
        category = _category(sums, rate, customer_tax)
        tax = _el(settlement, "ram:ApplicableTradeTax")
        _el(tax, "ram:CalculatedAmount", _amount(vat))
        _el(tax, "ram:TypeCode", "VAT")
        if category in EXEMPTION:
            _el(tax, "ram:ExemptionReason", EXEMPTION[category])
        _el(tax, "ram:BasisAmount", _amount(part))
        _el(tax, "ram:CategoryCode", category)
        if category != "O":  # BR-O-10
            _el(tax, "ram:RateApplicablePercent", f"{rate.normalize():f}")
    if days and days[0] != days[-1]:
        period = _el(settlement, "ram:BillingSpecifiedPeriod")
        _date(period, "ram:StartDateTime", days[0])
        _date(period, "ram:EndDateTime", days[-1])
    if due and not cancels:
        terms = _el(settlement, "ram:SpecifiedTradePaymentTerms")
        _el(terms, "ram:Description", f"Zahlbar bis {due.strftime('%d.%m.%Y')} ohne Abzug.")
        _date(terms, "ram:DueDateDateTime", due)
    summary = _el(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(summary, "ram:LineTotalAmount", _amount(sums.net))
    _el(summary, "ram:TaxBasisTotalAmount", _amount(sums.net))
    _el(summary, "ram:TaxTotalAmount", _amount(sums.vat), currencyID="EUR")
    _el(summary, "ram:GrandTotalAmount", _amount(sums.gross))
    _el(summary, "ram:DuePayableAmount", _amount(sums.gross))
    if cancels:
        reference = _el(settlement, "ram:InvoiceReferencedDocument")
        _el(reference, "ram:IssuerAssignedID", cancels)
        if cancels_date:
            formatted = _el(reference, "ram:FormattedIssueDateTime")
            _el(formatted, "qdt:DateTimeString", cancels_date.strftime("%Y%m%d"), format="102")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _line_rate(sums: Totals, line_rate, standard: Decimal, customer_tax: str) -> Decimal:
    if sums.vat_rate is None or customer_tax in ("eu", "drittland"):
        return Decimal(0)
    return standard if line_rate is None else line_rate


def embed(pdf: bytes, xml: bytes) -> bytes:
    """Put the XML into the PDF as factur-x.xml (PDF/A-3, ZUGFeRD 2 / Factur-X)."""
    from facturx import generate_from_binary

    return generate_from_binary(
        pdf, xml, flavor="factur-x", level="en16931", check_xsd=True, afrelationship="alternative"
    )
