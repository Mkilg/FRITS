"""Read an invoice Fritz wrote before FRITS, on this computer and without AI.

Fritz drops the PDF, photo or Word file of an old invoice on the Rechnungen page. Its text
comes from ingest.py (local OCR for photos); here FRITS guesses number, customer, dates,
amount and VAT with plain patterns, so the "Rechnung von früher nachtragen" form is mostly
filled in. Only what looks reasonably sure is returned; Fritz checks and types the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from frits.beleg import amounts, dates

# ---------- invoice number ----------

_NUMBER_LABEL = re.compile(
    r"(?i)\b(?:rechnungs[\s-]?(?:nummer|nr\.?)|re[\s.-]?nr\.?|rechnung\s+(?:nr\.?|nummer|no\.?|#)"
    r"|invoice\s+(?:no\.?|number|#))\s*[:#]?\s*"
)
_NUMBER_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}")
_RE_NUMBER = re.compile(r"\bRE[-_ ]?\d{1,4}(?:[-/.]\d{1,6})*\b")


def _clean_number(value: str) -> str | None:
    value = value.rstrip("./-")
    if not re.search(r"\d", value) or re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", value):
        return None  # "Rechnungsnummer: siehe unten" or a date in the wrong place
    return value


def _number(lines: list[str]) -> str | None:
    for i, line in enumerate(lines):
        for label in _NUMBER_LABEL.finditer(line):
            rest = line[label.end() :]
            if not rest.strip() and i + 1 < len(lines):
                rest = lines[i + 1]  # label on one line, the number below
            value = _NUMBER_VALUE.match(rest.strip())
            if value and (found := _clean_number(value.group())):
                return found
    for line in lines:
        if found := _RE_NUMBER.search(line):
            return found.group().replace(" ", "-")
    return None


# ---------- dates ----------

_ISSUE_LABEL = re.compile(
    r"(?i)(?:rechnungsdatum|ausstellungsdatum|rechnung\s+vom|(?<![a-zäöüß-])datum)\s*:?\s*$"
)
_SERVICE_LABEL = re.compile(
    r"(?i)(?:leistungsdatum|lieferdatum|ausführungsdatum|leistungszeitraum|lieferzeitraum"
    r"|datum\s+der\s+leistung|leistung\s+(?:erbracht\s+)?(?:am|vom)|zeitraum)\s*:?\s*"
)
_NOT_ISSUE = re.compile(
    r"(?i)(?:fällig|zahlbar|zahlungsziel|bis\s+zum|bis\s*$|spätestens|leistung|liefer|zeitraum"
    r"|geburt|bestell|auftrag)"
)
# "Musterstadt, 12. März 2025" or "Musterstadt, den 12.03.2025" above the letter.
_PLACE_DATE = re.compile(r"(?m)^[A-ZÄÖÜ][\w .()/-]{1,40},\s*(?:den\s+)?$")


def _dates_with_context(text: str, today: date) -> list[tuple[int, date, str]]:
    """Every day in the text that isn't in the future, with the text just before it."""
    found = []
    for start, day in sorted(dates(text)):
        if day <= today:
            line_start = text.rfind("\n", 0, start) + 1
            found.append((start, day, text[max(line_start, start - 60) : start]))
    return found


def _issue_date(found: list[tuple[int, date, str]]) -> date | None:
    for _, day, before in found:
        if _ISSUE_LABEL.search(before) or _PLACE_DATE.search(before):
            return day
    for _, day, before in found:
        if not _NOT_ISSUE.search(before) and not _SERVICE_LABEL.search(before):
            return day
    return None


def _service_date(text: str, found: list[tuple[int, date, str]]) -> date | None:
    """The day of the work, or the last day of a period ("01.02.2025 – 28.02.2025")."""
    for i, (start, day, before) in enumerate(found):
        if not _SERVICE_LABEL.search(before):
            continue
        if i + 1 < len(found):
            following_start, following, between = found[i + 1]
            gap = text[start:following_start]
            if "\n" not in gap and re.search(r"(?:-|–|bis)\s*$", between):
                return following
        return day
    return None


# ---------- amounts and VAT ----------

_KLEIN = re.compile(
    r"(?i)§\s*19\s*(?:abs\.?\s*\d\s*)?ustg|kleinunternehmer|keine\s+umsatzsteuer\s+(?:berechnet|ausgewiesen)"
    r"|umsatzsteuerbefreit\s+nach\s+§\s*19"
)
# Reverse charge or export: FRITS can't tell which of its choices fits, Fritz picks it.
_SPECIAL_TAX = re.compile(
    r"(?i)reverse[\s-]charge|steuerschuldnerschaft\s+des\s+leistungsempfängers|§\s*13b"
    r"|innergemeinschaftlich|nicht\s+(?:im\s+inland\s+)?steuerbar|drittland"
)
_RATE = re.compile(r"(?<![\d,.])(19|7|16|5)(?:[,.]0{1,2})?\s*%")
_PERCENT = re.compile(r"\d+(?:[,.]\d+)?\s*%")

# Best first: the first label that has an amount next to it wins.
_NET_LABELS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:rechnungs|gesamt)?betrag\s+netto|summe\s+netto|gesamt\s+netto|nettosumme",
        r"netto(?:betrag)?",
        r"zwischensumme",
    )
)
_GROSS_LABELS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"gesamtbetrag|rechnungsbetrag|bruttobetrag|endbetrag|zahlbetrag",
        r"summe\s+brutto|gesamt\s+brutto|bruttosumme|gesamtsumme|zu\s+zahlen",
        r"\bbrutto\b|\bgesamt\s*:|\bsumme\s*:|\btotal\b",
    )
)
_ANY_NET = re.compile("|".join(p.pattern for p in _NET_LABELS), re.IGNORECASE)
_VAT_LABEL = re.compile(r"(?i)umsatzsteuer|mehrwertsteuer|\bust\b|\bmwst\b|\bmwst\.")


def _decimal(amount: str) -> Decimal:
    return Decimal(amount.replace(",", "."))


def _labeled_amount(lines: list[str], labels, skip: re.Pattern | None = None):
    """The amount next to the best of `labels`: on the same line after it, or alone on the
    next line."""
    if isinstance(labels, re.Pattern):
        labels = (labels,)
    for label in labels:
        if (found := _amount_after(lines, label, skip)) is not None:
            return found
    return None


def _amount_after(lines: list[str], label: re.Pattern, skip: re.Pattern | None):
    for i, line in enumerate(lines):
        match = label.search(line)
        if not match or (skip and skip.search(line)):
            continue
        rest = _PERCENT.sub(" ", line[match.end() :])
        found = amounts(rest)
        if not found and not rest.strip() and i + 1 < len(lines):
            below = _PERCENT.sub(" ", lines[i + 1])
            if sum(ch.isalpha() for ch in below) <= 4:  # just "1.200,00 €", not a new row
                found = amounts(below)
        if found:
            return _decimal(found[-1])
    return None


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _german(value: Decimal) -> str:
    """Decimal("1200.5") -> "1.200,50", the way Fritz types it."""
    return f"{_cents(value):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _tax_and_net(lines: list[str], text: str) -> tuple[str | None, Decimal | None]:
    if _KLEIN.search(text):
        net = _labeled_amount(lines, _GROSS_LABELS) or _labeled_amount(lines, _NET_LABELS)
        return "klein", _cents(net) if net is not None else None
    net = _labeled_amount(lines, _NET_LABELS, skip=re.compile(r"(?i)brutto"))
    if _SPECIAL_TAX.search(text):
        return None, net
    rates = {m.group(1) for m in _RATE.finditer(text)}
    tax = rates.pop() if len(rates) == 1 and rates <= {"19", "7"} else None
    if net is None and tax:
        gross = _labeled_amount(lines, _GROSS_LABELS, skip=_ANY_NET)
        vat = _labeled_amount(lines, _VAT_LABEL, skip=_ANY_NET)
        if gross is not None and vat is not None and _cents(gross - vat) > 0:
            expected = _cents((gross - vat) * Decimal(tax) / 100)
            if abs(expected - vat) <= Decimal("0.02"):
                net = gross - vat
        if net is None and gross is not None:
            net = gross / (1 + Decimal(tax) / 100)
    return tax, _cents(net) if net is not None else None


# ---------- customer ----------

_POSTCODE_LINE = re.compile(r"^(?:D[-\s])?\d{5}\s+[A-ZÄÖÜ]")
_SENDER_HINTS = re.compile(
    r"(?i)\btel\b|telefon|mobil|e-?mail|@|iban|bic\b|steuer-?\s?nr|steuernummer|ust-?id|www\.|http"
)
_SALUTATION = re.compile(r"(?i)^(?:an\s*:?|herrn?|frau|firma|z\.\s*hd\.?.*|zu\s+händen.*)$")
_CUSTOMER_LABEL = re.compile(
    r"(?i)^(?:kunde|kundin|rechnungsempfänger(?:in)?|empfänger(?:in)?|rechnung\s+an|an)\s*:\s*"
)
_STREET = re.compile(
    r"(?i)(?:str\.|straße|strasse|weg|allee|platz|gasse|ring|damm|ufer|chaussee)\s*\d"
    r"|^\D+\s\d{1,4}\s?[a-z]?$|postfach"
)
_NOT_CUSTOMER = re.compile(r"(?i)^(?:rechnung|seite|datum|kunden-?nr|lieferschein|angebot)\b")


def _own_marks(own: Iterable[str]) -> list[str]:
    """Pieces of Fritz's own details ("Fritz Beispiel", "12345 Musterstadt") to recognise them."""
    marks = []
    for value in own:
        for part in re.split(r"[,\n]", value or ""):
            part = " ".join(part.split()).casefold()
            if len(part) >= 5:
                marks.append(part)
    return marks


def _is_own(line: str, marks: list[str]) -> bool:
    folded = " ".join(line.split()).casefold()
    return any(mark in folded for mark in marks)


def _sender_line(line: str) -> bool:
    """'Fritz Beispiel · Werkstattweg 1 · 12345 Musterstadt': the small line above the address."""
    return bool(re.search(r"\d{5}\s+\w", line)) and bool(re.search(r"\s[·|•–-]\s|,\s", line))


def _customer(lines: list[str], marks: list[str]) -> str | None:
    for i, line in enumerate(lines):
        label = _CUSTOMER_LABEL.match(line)
        if label:
            value = line[label.end() :].strip() or (lines[i + 1] if i + 1 < len(lines) else "")
            value = value.split(",")[0].strip()
            if value and not _is_own(value, marks) and sum(ch.isalpha() for ch in value) >= 3:
                return value[:120]
    for i, line in enumerate(lines):
        if not _POSTCODE_LINE.match(line) or _is_own(line, marks):
            continue
        start = i
        while start > 0 and i - start < 5:
            above = lines[start - 1]
            if not above or _sender_line(above) or _POSTCODE_LINE.match(above):
                break
            if _is_own(above, marks) or _NOT_CUSTOMER.match(above) or dates(above):
                break
            start -= 1
        block = lines[start:i]
        context = lines[start : i + 3]
        if not block or any(_SENDER_HINTS.search(x) for x in context):
            continue  # Fritz's own letterhead or footer: phone, mail, bank
        for candidate in block:
            if _SALUTATION.match(candidate) or _STREET.search(candidate):
                continue
            candidate = re.sub(r"(?i)^(?:firma|herrn?|frau)\s+", "", candidate).strip()
            if sum(ch.isalpha() for ch in candidate) >= 3:
                return candidate[:120]
    return None


# ---------- what it was for ----------

_TABLE_HEAD = re.compile(r"(?i)\b(?:beschreibung|bezeichnung|leistung|artikel|tätigkeit)\b")
_DESCRIPTION_LABEL = re.compile(r"(?i)^(?:leistung|leistungsbeschreibung|betreff|projekt)\s*:\s*")
_UNIT_WORDS = re.compile(
    r"(?i)(?<![\w])(?:std\.?|stunden?|stk\.?|stück|pauschal|psch\.?|tage?|km|h|x|eur|€)(?![\w])"
)


def _description(lines: list[str]) -> str | None:
    for line in lines:
        label = _DESCRIPTION_LABEL.match(line)
        if label:
            value = line[label.end() :].strip()
            if value and not re.match(r"(?i)rechnung\b", value):
                return value[:200]
    for i, line in enumerate(lines):
        if not _TABLE_HEAD.search(line) or amounts(line):
            continue
        for row in lines[i + 1 : i + 4]:
            if not amounts(row) or _ANY_NET.search(row) or _GROSS_LABELS[0].search(row):
                continue
            words = _PERCENT.sub(" ", row)
            words = re.sub(r"(?<![\w])\d[\d.,]*(?![\w])", " ", words)
            words = _UNIT_WORDS.sub(" ", words)
            words = " ".join(words.split()).strip(" -–:|")
            if sum(ch.isalpha() for ch in words) >= 3:
                return words[:200]
        break
    return None


# ---------- all together ----------


def lesen(
    text: str, own_name: str | Iterable[str] | None = None, today: date | None = None
) -> dict[str, str]:
    """Suggestions for the "Rechnung von früher" form, keys only for what was found:
    number, customer, issue_date, service_date (ISO), description, net ("1.200,00"),
    tax (a key of billing.EARLIER_TAX). `own_name` is Fritz's name, or several of his own
    details (name, company, address), so his letterhead is never taken for the customer."""
    today = today or date.today()
    own = [own_name] if isinstance(own_name, str) else list(own_name or ())
    lines = [" ".join(line.split()) for line in text.splitlines()]
    marks = _own_marks(own)
    found_dates = _dates_with_context(text, today)
    issued = _issue_date(found_dates)
    served = _service_date(text, found_dates)
    tax, net = _tax_and_net([line for line in lines if line], text)
    result = {
        "number": _number([line for line in lines if line]),
        "customer": _customer(lines, marks),
        "issue_date": issued.isoformat() if issued else None,
        "service_date": served.isoformat() if served and served != issued else None,
        "description": _description([line for line in lines if line]),
        "net": _german(net) if net is not None and net > 0 else None,
        "tax": tax,
    }
    return {key: value for key, value in result.items() if value}
