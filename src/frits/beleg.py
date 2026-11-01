"""Read the obvious parts of a receipt (Beleg) on this computer, without AI.

A receipt photo is turned into text by local OCR (see ingest.py). From that text FRITS
guesses the total, the day it was paid, the shop and the VAT rate, so Fritz only has to
check four fields instead of typing them. Every guess is shown as a suggestion; nothing is
saved until he presses "Speichern".
"""

from __future__ import annotations

import re
from datetime import date

# "12,34" or "1.234,56" or "12.34" (some till rolls), optionally with € or EUR around it.
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})*|\d+)[,.](\d{2})(?![\d])")
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MONTHS = (
    "januar",
    "februar",
    "märz",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "dezember",
)
_WORD_DATE = re.compile(
    r"\b(\d{1,2})\.?\s*(" + "|".join(_MONTHS) + r"|maerz)\s+(\d{4})\b", re.IGNORECASE
)

# Words that sit next to the amount actually paid, best first.
_TOTAL_WORDS = (
    r"zu\s*zahlen",
    r"gesamtbetrag",
    r"rechnungsbetrag",
    r"endbetrag",
    r"gesamt",
    r"summe",
    r"total",
    r"betrag",
    r"preis",
    r"bar\b",
    r"ec[\s-]*karte|girocard|kartenzahlung|visa|mastercard",
)
# Lines that are clearly not the total even though they carry an amount.
_NOT_TOTAL = re.compile(
    r"mwst|ust\b|umsatzsteuer|netto|steuer|rückgeld|rueckgeld|gegeben", re.IGNORECASE
)
_NOT_A_NAME = re.compile(
    r"^(rechnung|quittung|beleg|kassenbon|bon|kasse|datum|uhrzeit|tel|fax|www\.|http|"
    r"steuer|ust|mwst|filiale|beleg-?nr|danke|vielen dank|willkommen|fahrkarte|fahrschein|"
    r"ticket|parkschein|einzelfahrt|tageskarte|bestellbestätigung|auftragsbestätigung)",
    re.IGNORECASE,
)


def _money(whole: str, cents: str) -> str:
    euros = int(re.sub(r"[.\s]", "", whole))
    return f"{euros},{cents}"


def _amounts(line: str) -> list[str]:
    line = _DATE.sub(" ", _ISO_DATE.sub(" ", line))  # "28.10.2026" is a day, not 28,10 €
    line = re.sub(r"\b\d{1,2}[:.]\d{2}\s*(uhr|h)\b", " ", line, flags=re.IGNORECASE)
    return [_money(m.group(1), m.group(2)) for m in _AMOUNT.finditer(line)]


def _as_number(amount: str) -> float:
    return float(amount.replace(",", "."))


def _before_tax(line: str) -> str:
    """'Preis 89,90 EUR MwSt 7% 5,88 EUR' -> 'Preis 89,90 EUR': what comes before the tax."""
    tax = _NOT_TOTAL.search(line)
    return line[: tax.start()] if tax else line


def guess_total(text: str) -> str | None:
    lines = [line for line in text.splitlines() if line.strip()]
    for word in _TOTAL_WORDS:
        pattern = re.compile(word, re.IGNORECASE)
        for i, line in enumerate(lines):
            line = _before_tax(line)
            if not pattern.search(line):
                continue
            found = _amounts(line)
            if not found and i + 1 < len(lines):
                found = _amounts(lines[i + 1])  # "Summe" on one line, the amount below
            if found:
                return found[-1]
    every = [a for line in lines if not _NOT_TOTAL.search(line) for a in _amounts(line)]
    return max(every, key=_as_number) if every else None


# Words before the day it was paid: that day counts, not the travel day on a ticket.
_PAID_WORDS = re.compile(
    r"(?i)(?:bezahlt|gezahlt|zahlung|bezahlung|zahldatum|belegdatum|kaufdatum|rechnungsdatum|"
    r"bestelldatum|gebucht)[^\n\d]{0,40}$"
)


def guess_date(text: str, today: date) -> str | None:
    """The day it was paid if the receipt says so, else the first real day on it that isn't
    in the future, as YYYY-MM-DD."""
    candidates = []
    for m in _DATE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        candidates.append((m.start(), year + 2000 if year < 100 else year, month, day))
    for m in _ISO_DATE.finditer(text):
        candidates.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _WORD_DATE.finditer(text):
        word = m.group(2).lower().replace("maerz", "märz")
        candidates.append((m.start(), int(m.group(3)), _MONTHS.index(word) + 1, int(m.group(1))))

    def paid_first(candidate: tuple) -> tuple:
        start = candidate[0]
        return (not _PAID_WORDS.search(text[max(0, start - 50) : start]), start)

    for _, year, month, day in sorted(candidates, key=paid_first):
        try:
            found = date(year, month, day)
        except ValueError:
            continue
        if found <= today and (today - found).days < 3 * 366:
            return found.isoformat()
    return None


# A receipt from abroad: its VAT is not German Vorsteuer (§ 15 UStG covers German VAT only),
# so the whole amount is the cost. Hotels in Vienna, trains in Switzerland, ...
_ABROAD = re.compile(
    r"\b(österreich|oesterreich|austria|wien|vienna|schweiz|suisse|svizzera|switzerland|"
    r"z[üu]rich|italia|france|nederland|belgi[eë]|danmark|polska|česk|espa[ñn]a)\b"
    r"|\bATU\s?\d{8}\b|\bCHE-?\d{3}|\bMWST-?Nr\b|\bUID\b|\bTVA\b|\bBTW\b|\bIVA\b",
    re.IGNORECASE,
)


def guess_vat(text: str) -> str | None:
    """ "0.07" or "0.19" when the receipt names exactly one of them; "" for a receipt from
    abroad, whose VAT FRITS must not count as Vorsteuer."""
    if _ABROAD.search(text):
        return ""
    rates = set()
    for m in re.finditer(r"(?<![\d,.])(19|7)(?:[,.]0{1,2})?\s*%", text):
        rates.add("0.19" if m.group(1) == "19" else "0.07")
    return rates.pop() if len(rates) == 1 else None


_MAIL_FROM = re.compile(r"(?im)^(?:from|von)\s*:\s*(.+)$")
_MAIL_HEADER = re.compile(r"(?i)^(?:from|von|to|an|cc|subject|betreff|date|datum)\s*:")


# Sender names that say nothing about the shop ("Rechnung <noreply@adobe.com>"): take the domain.
_GENERIC_SENDER = re.compile(
    r"(?i)(?:ihre\s+)?(?:rechnung(?:en)?|invoice|billing|noreply|no-reply|no reply|service|"
    r"kundenservice|customer service|info|team|support|buchhaltung|bestellung|order|shop)"
)


def _mail_sender(header: str) -> str | None:
    """'Thomann <rechnung@thomann.de>' -> 'Thomann'; a bare address -> its domain name."""
    name = header.split("<")[0].strip().strip('"').strip()
    if name and "@" not in name and not _GENERIC_SENDER.fullmatch(name):
        return name[:60]
    address = re.search(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", header)
    if not address:
        return None
    parts = address.group(1).split(".")
    return (parts[-2] if len(parts) >= 2 else parts[0]).capitalize()


# Words after a shop's name that name the paper, not the shop.
_DOC_WORDS = re.compile(
    r"(?i)\s+(?:buchungsbestätigung|bestellbestätigung|auftragsbestätigung|rechnung|quittung|"
    r"kassenbon|handy-ticket|online-ticket|e-ticket|ticket)\b.*$"
)


def guess_vendor(text: str) -> str | None:
    """Shops print their name at the top: the first line that looks like a name.
    In a mail the shop is the sender."""
    sender = _MAIL_FROM.search("\n".join(text.splitlines()[:12]))
    if sender and (found := _mail_sender(sender.group(1))):
        return found
    for line in text.splitlines()[:8]:
        if _MAIL_HEADER.match(line.strip()):
            continue
        line = _DOC_WORDS.sub("", line).strip()
        line = re.sub(r"\s+", " ", line).strip(" *-=|_.:")
        letters = sum(ch.isalpha() for ch in line)
        if letters < 3 or letters < len(line) / 2 or _NOT_A_NAME.match(line):
            continue
        if _AMOUNT.search(line) or _DATE.search(line) or _WORD_DATE.search(line):
            continue
        return line[:60]
    return None


# Words on a receipt that point to one expense category. First match wins, so the more
# specific ones come first. Only a suggestion: Fritz sees it in the form and can change it.
_CATEGORY_WORDS = (
    ("bewirtung", r"bewirtung|restaurant|gastst[äa]tte|caf[ée]\b|trinkgeld"),
    (
        "reisekosten",
        (
            r"deutsche bahn|\bdb\b|fahrkarte|bahncard|ticket|flixbus|\bice\b|\bic\b|hotel"
            r"|übernachtung|parkhaus|parkschein|taxi|flug|lufthansa|\bbvg\b|\bmvg\b|\bhvv\b"
        ),
    ),
    ("telekommunikation", r"telekom|vodafone|\bo2\b|1&1|mobilfunk|\bdsl\b"),
    ("fortbildung", r"seminar|kurs|workshop|fachbuch|konferenz|tagung"),
    ("buero", r"porto|briefmarke|deutsche post|dhl|druckerpatrone|toner|papier|büro"),
    ("software", r"abo\b|abonnement|subscription|software|lizenz|adobe|microsoft|apple\.com"),
    ("waren", r"baumarkt|obi\b|hornbach|bauhaus|toom|material"),
)


def guess_category(text: str) -> str | None:
    lowered = text.casefold()
    for key, pattern in _CATEGORY_WORDS:
        if re.search(pattern, lowered):
            return key
    return None


def guess(text: str, today: date) -> dict[str, str]:
    """Suggestions for the expense form. Keys only for what was found."""
    found = {
        "gross": guess_total(text),
        "date": guess_date(text, today),
        "vendor": guess_vendor(text),
        "vat_rate": guess_vat(text),
        "category": guess_category(text),
    }
    return {
        key: value for key, value in found.items() if value or (key == "vat_rate" and value == "")
    }


# Words that only a bought ticket or a till receipt carries, not a note about a job.
_RECEIPT_WORDS = re.compile(
    r"\b(fahrkarte|fahrschein|online-ticket|ticket|bahncard|quittung|kassenbon|kassenbeleg|"
    r"tankbeleg|bewirtungsbeleg|deutsche bahn|db fernverkehr|db regio|flixbus|bon-nr)\b",
    re.IGNORECASE,
)


def looks_like_receipt(text: str) -> bool:
    """A ticket or till receipt: a receipt word and a paid amount. Then FRITS can take it as
    a Beleg without any AI, even if the AI may not see it (e.g. redaction wasn't sure)."""
    return bool(_RECEIPT_WORDS.search(text)) and guess_total(text) is not None
