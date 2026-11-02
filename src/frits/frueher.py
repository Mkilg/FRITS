"""Read an invoice Fritz wrote before FRITS, on this computer and without AI.

Fritz drops the PDF, photo or Word file of an old invoice on the Rechnungen page. Its text
comes from ingest.py (local OCR for photos); here FRITS guesses number, customer, dates,
amount and VAT with plain patterns, so the "Rechnung von früher nachtragen" form is mostly
filled in. Only what looks reasonably sure is returned; Fritz checks and types the rest.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

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


_DUE = re.compile(
    r"(?i)(?:zahlbar|fällig|zahlungsziel|überweisen|bis\s+zum|bis\s+spätestens|spätestens)[^\n]{0,30}$"
)


def _due_date(found: list[tuple[int, date, str]], issued: date | None) -> date | None:
    """The day the money was due ("zahlbar bis 26.03.2025"), if the invoice says so."""
    for _, day, before in found:
        if _DUE.search(before) and (issued is None or day > issued):
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


# Lines that open a letter or say how to pay: never "what it was for".
_NOT_DESCRIPTION = re.compile(
    r"(?i)^(?:sehr\s+geehrte|liebe[rs]?\b|hallo|guten\s+tag|vielen\s+dank|mit\s+freundlichen"
    r"|hiermit|ich\s+(?:erlaube|stelle|berechne)|wie\s+(?:vereinbart|besprochen)|bitte|zahlbar"
    r"|gemäß|gem\.|pos\.?\b|seite\b|kunden-?nr|ihre?\s|unser|rechnung|invoice|datum\b|steuer"
    r"|ust|mwst|iban|bic\b|bank|tel|e-?mail|www\.|leistungs|liefer|zeitraum)"
)
_DATE_WORDS = re.compile(
    r"\b\d{1,2}\.\s?(?:\d{1,2}\.\s?|(?:jan|feb|mär|apr|mai|jun|jul|aug|sep|okt|nov|dez)[a-zä]*\.?\s)"
    r"(?:\d{4}|\d{2})?\b",
    re.IGNORECASE,
)


def _free_description(lines: list[str], marks: list[str], customer: str | None):
    """A Word invoice without a table: the first plain line below the number that names the
    work ("Moderation Podiumsdiskussion „KI im Radio“, 10.02.2025: 1.200,00 €"). Returns the
    words and a day named in that line, which is then the day of the work."""
    start = next(
        (
            i + 1
            for i, line in enumerate(lines)
            if _NUMBER_LABEL.search(line) or _RE_NUMBER.search(line)
        ),
        None,
    )
    if start is None:
        return None, None
    for line in lines[start : start + 12]:
        if _ANY_NET.search(line) or _VAT_LABEL.search(line):
            break
        if any(label.search(line) for label in _GROSS_LABELS):
            break
        if (
            _NOT_DESCRIPTION.match(line)
            or _CUSTOMER_LABEL.match(line)
            or _POSTCODE_LINE.match(line)
            or _SALUTATION.match(line)
            or _SENDER_HINTS.search(line)
            or _is_own(line, marks)
            or (customer and customer.casefold() in line.casefold())
            or _STREET.search(line)
        ):
            continue
        days = [day for _, day in dates(line)]
        words = _DATE_WORDS.sub(" ", line)
        words = _PERCENT.sub(" ", words)
        words = re.sub(r"(?<![\w])\d[\d.,]*(?![\w])", " ", words)
        words = _UNIT_WORDS.sub(" ", words)
        words = " ".join(words.split()).strip(" -–:|,;")
        # One word is enough when the line also names a day or an amount
        # ("Jahresrückblick-Moderation, 18.12.2025"); a lone word could be anything.
        letters = sum(ch.isalpha() for ch in words)
        alone = len(words.split()) == 1 and letters >= 8 and (days or re.search(r"\d,\d\d\b", line))
        if letters >= 5 and (len(words.split()) >= 2 or alone):
            return words[:200], days[0] if len(days) == 1 else None
    return None, None


# ---------- all together ----------


def lesen(
    text: str, own_name: str | Iterable[str] | None = None, today: date | None = None
) -> dict[str, str]:
    """Suggestions for the "Rechnung von früher" form, keys only for what was found:
    number, customer, issue_date, service_date, due_date (ISO), description, net ("1.200,00"),
    tax (a key of billing.EARLIER_TAX). `own_name` is Fritz's name, or several of his own
    details (name, company, address), so his letterhead is never taken for the customer."""
    today = today or date.today()
    own = [own_name] if isinstance(own_name, str) else list(own_name or ())
    lines = [" ".join(line.split()) for line in text.splitlines()]
    marks = _own_marks(own)
    found_dates = _dates_with_context(text, today)
    issued = _issue_date(found_dates)
    served = _service_date(text, found_dates)
    filled = [line for line in lines if line]
    tax, net = _tax_and_net(filled, text)
    customer = _customer(lines, marks)
    description = _description(filled)
    if description is None:
        description, day = _free_description(filled, marks, customer)
        if served is None and day is not None and day <= today:
            served = day
    due = _due_date(found_dates, issued)
    result = {
        "number": _number(filled),
        "customer": customer,
        "issue_date": issued.isoformat() if issued else None,
        "service_date": served.isoformat() if served and served != issued else None,
        "due_date": due.isoformat() if due else None,
        "description": description,
        "net": _german(net) if net is not None and net > 0 else None,
        "tax": tax,
    }
    return {key: value for key, value in result.items() if value}


# ---------- Fritz's own invoice, dropped where receipts go ----------

_INVOICE_WORD = re.compile(r"(?i)\brechnung|\binvoice\b")
# Documents that name Fritz's bank account or tax number but aren't his invoice: a direct
# debit, a payment notice, a bank statement, a letter from the tax office.
_NOT_OWN_INVOICE = re.compile(
    r"(?i)lastschrift|abgebucht|abbuchung|von\s+ihrem\s+konto|auf\s+ihr\s+konto|mandat"
    r"|gutschrift|kontoauszug|kontostand|saldo|finanzamt|bescheid|lohnsteuerbescheinigung"
)


def _compact(value: str) -> str:
    return re.sub(r"[\s./-]", "", value or "").casefold()


def eigene_rechnung(text: str, settings) -> bool:
    """Is this an invoice Fritz wrote himself (before FRITS), not one he received?

    It has to name his bank account or tax number, the way his own invoices do, and carry his
    name or company in the letterhead at the top. A receipt addressed to him has his name
    there too, but not his IBAN or Steuernummer; a direct debit with his IBAN says so.
    """
    if not settings.name or not _INVOICE_WORD.search(text) or _NOT_OWN_INVOICE.search(text):
        return False
    compact = _compact(text)
    keys = [_compact(settings.iban), _compact(settings.steuernummer)]
    if not any(len(key) >= 8 and key in compact for key in keys):
        return False
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if _number(lines) is None:
        return False
    marks = _own_marks((settings.name, settings.company))
    return any(_is_own(line, marks) for line in lines[:6])


def vielleicht_eigene_rechnung(text: str, settings) -> bool:
    """Might this be Fritz's own old invoice even without his IBAN or tax number on it?

    His name or company opens the page (the letterhead), it is an invoice with a number, and
    it goes to someone else. Not sure enough to decide, so FRITS only asks.
    """
    if not settings.name or not _INVOICE_WORD.search(text) or _NOT_OWN_INVOICE.search(text):
        return False
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    marks = _own_marks((settings.name, settings.company))
    if _number(lines) is None or not any(_is_own(line, marks) for line in lines[:2]):
        return False
    customer = lesen(text, (settings.name, settings.company, settings.address)).get("customer")
    return bool(customer) and not _is_own(customer, marks)


# ---------- waiting for Fritz to check ----------
# An old invoice waits in ws.staging/<token>/ while Fritz checks what FRITS read from it: the
# file itself and "gelesen.json" with the guesses ("job" when it came in through Heute).


def merken(folder: Path, datei: str | None, felder: dict, job: int | None = None) -> None:
    data = {"datei": datei, "felder": felder}
    if job is not None:
        data["job"] = job
    (folder / "gelesen.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def vormerken(staging: Path, source: Path, text: str, settings, today: date, job: int) -> str:
    """Put a copy of Fritz's own old invoice where the "Rechnung von früher" form finds it,
    read and ready to check. Returns the token for /invoices?frueher=<token>."""
    token = secrets.token_urlsafe(16)
    folder = Path(staging) / token
    folder.mkdir(parents=True)
    shutil.copy2(source, folder / source.name)
    felder = lesen(text, (settings.name, settings.company, settings.address), today)
    merken(folder, source.name, felder, job)
    return token
