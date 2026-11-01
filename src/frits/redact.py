"""Replace personal data with placeholders before any text leaves the machine.

Four layers run on every text: known customers, patterns (IBAN, email, phone, ...), a salutation
rule ("Frau Berger"), and spaCy NER. No language model takes part. The mapping from placeholder
back to the real value stays local and is never sent anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from frits.customers import Customer

NER_MODELS = ("de_core_news_lg", "de_core_news_md", "de_core_news_sm")

_W = r"[\wäöüÄÖÜß]"
_CAP = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
_SP = r"[ \t]+"  # never across a line break: lines are separate facts

# Layer priority: lower wins when spans overlap.
CUSTOMER, PATTERN, SALUTATION, NER = range(4)

# NER sometimes tags units, weekdays or work words; replacing them would break extraction.
_NER_STOPWORDS = {
    "std",
    "std.",
    "stunden",
    "stunde",
    "km",
    "stk",
    "stück",
    "pauschal",
    "rechnung",
    "rechnungen",
    "angebot",
    "montag",
    "dienstag",
    "mittwoch",
    "donnerstag",
    "freitag",
    "samstag",
    "sonntag",
    "euro",
    "eur",
    "mwst",
    "ust",
    "gartenarbeit",
    "danke",
    "grüße",
    "gruß",
    "hallo",
    "frits",
}
# Work locations the invoice needs; NER tags them as places but they identify nobody.
_LOCATION_ALLOW = {
    "garten",
    "keller",
    "dach",
    "küche",
    "bad",
    "büro",
    "haus",
    "wohnung",
    "hof",
    "terrasse",
    "balkon",
    "garage",
    "flur",
    "treppenhaus",
    "dachboden",
}

_STREET_SUFFIX = (
    r"(?:straße|strasse|str\.|weg|gasse|allee|platz|ring|damm|ufer|steig|pfad|chaussee)"
)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("USTID", re.compile(r"\bDE\s?\d{9}\b")),
    ("STEUERNR", re.compile(r"\b\d{2,3}/\d{3,4}/\d{4,5}\b")),
    ("BIC", re.compile(r"\b[A-Z]{4}(?:DE|AT|CH)[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")),
    (
        "TEL",
        re.compile(r"(?<![\w+])(?:\+49|0049|0)[ \t/()-]*\d(?:[ \t/()-]*\d){5,13}(?!\d)"),
    ),
    (
        "ADRESSE",
        re.compile(
            rf"\b(?:{_CAP}(?:{_SP}|-)){{0,2}}(?:[A-ZÄÖÜ][a-zäöüß]*{_STREET_SUFFIX}"
            r"|Straße|Strasse|Weg|Gasse|Allee|Platz|Ring|Damm|Ufer)"
            rf"{_SP}\d{{1,4}}[ \t]?[a-z]?\b"
        ),
    ),
    (
        "ADRESSE",
        re.compile(rf"\b(?:Am|An der|Im|Auf der|Zum|Zur){_SP}{_CAP}{_SP}\d{{1,4}}[ \t]?[a-z]?\b"),
    ),
    (
        "ORT",
        re.compile(
            rf"\b\d{{5}}{_SP}(?!(?:Euro|EUR|Stunden|Stück|Kilometer|km)\b){_CAP}"
            rf"(?:{_SP}(?:an der|am|im|bei){_SP}{_CAP})?"
        ),
    ),
]
# Steuerliche Identifikationsnummer (§ 139b AO): 11 digits, often grouped 2-3-3-3. Redacted
# when its check digit is right or a label stands just before it (a typo'd ID is still his).
_STEUER_ID = re.compile(r"(?<![\d/.,])\d{2}[ \t]?\d{3}[ \t]?\d{3}[ \t]?\d{3}(?![\d/.,]\d)")
_STEUER_ID_LABEL = re.compile(
    r"(?i)(?:identifikations-?\s?nummer|steuer-?\s?id|id-?\s?nr\.?|idnr\.?|\btin\b|"
    r"steuerliche[n]?\s+id)[^\n\d]{0,25}$"
)
# Sozialversicherungs- / Rentenversicherungsnummer: 2 digits, birth date (6), letter, 3 digits.
_SVNR = re.compile(r"\b\d{2}[ \t]?\d{6}[ \t]?[A-Z][ \t]?\d{3}\b")
_BIRTHDATE = re.compile(r"(?i)\b(?:geb\.|geboren(?:\s+am)?)\s*(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4})")
_IBAN_START = re.compile(r"\b[A-Z]{2}\d{2}(?=[\sA-Z0-9])")
_SALUTATION = re.compile(
    rf"\b(?:Herrn?|Frau|Hr\.|Fr\.){_SP}(?:(?:Dr\.|Prof\.){_SP})*"
    rf"({_CAP}(?:{_SP}(?!(?:und|oder)\b){_CAP})?)"
    rf"|\b(?:Dr\.|Prof\.){_SP}({_CAP}(?:{_SP}{_CAP})?)"
)
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")


class LeakError(Exception):
    """Redacted text still contains personal data. Nothing may be sent."""


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    key: str
    canonical: str
    layer: int


@dataclass
class Mapping:
    """placeholder -> {kind, value, surface}. `value` is what re-identification puts back."""

    entries: dict[str, dict] = field(default_factory=dict)

    def values(self) -> list[str]:
        out = []
        for entry in self.entries.values():
            out.append(entry["value"])
            out.extend(entry["surface"])
        return out

    def reidentify(self, text: str) -> str:
        return _PLACEHOLDER.sub(
            lambda m: self.entries[m.group()]["value"] if m.group() in self.entries else m.group(),
            text,
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Mapping:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RedactedText:
    text: str
    mapping: Mapping


@lru_cache(maxsize=1)
def load_ner():
    """Load the best installed German model. Missing model is an error: redaction never runs half-done."""
    import spacy

    for name in NER_MODELS:
        try:
            return spacy.load(name, exclude=["parser", "lemmatizer", "morphologizer"])
        except OSError:
            continue
    raise RuntimeError(
        "Das deutsche Sprachmodell zum Schwärzen fehlt auf diesem Computer. Ohne es schickt "
        "FRITS aus Sicherheit nichts an eine KI. In der FRITS-App ist es eingebaut; wenn du "
        "FRITS selbst installiert hast, steht in der Anleitung (README), wie du es nachlädst."
    )


def redact(text: str, customers: list[Customer] = (), nlp=None) -> RedactedText:
    nlp = load_ner() if nlp is None else nlp
    spans = (
        _customer_spans(text, customers)
        + _pattern_spans(text)
        + _salutation_spans(text)
        + _ner_spans(text, nlp)
    )
    chosen = _resolve(spans)

    mapping = Mapping()
    placeholder_for: dict[str, str] = {}
    counters: dict[str, int] = {}
    for span in chosen:
        if span.key not in placeholder_for:
            counters[span.kind] = counters.get(span.kind, 0) + 1
            ph = f"[{span.kind}_{counters[span.kind]}]"
            placeholder_for[span.key] = ph
            mapping.entries[ph] = {"kind": span.kind, "value": span.canonical, "surface": []}
        surface = text[span.start : span.end]
        entry = mapping.entries[placeholder_for[span.key]]
        if surface not in entry["surface"]:
            entry["surface"].append(surface)

    out, last = [], 0
    for span in chosen:
        out.append(text[last : span.start])
        out.append(placeholder_for[span.key])
        last = span.end
    out.append(text[last:])
    redacted = RedactedText("".join(out), mapping)
    assert_no_leaks(redacted, customers)
    return redacted


def assert_no_leaks(redacted: RedactedText, customers: list[Customer] = ()) -> None:
    """Final gate before sending: no mapped value, known customer or pattern match may remain."""
    text = redacted.text
    terms = [v for v in redacted.mapping.values() if len(v.strip()) >= 3]
    for customer in customers:
        terms.extend(t for t, _ in _customer_terms(customer) if len(t) >= 3)
    for term in terms:
        if _word_regex(term).search(text):
            raise LeakError(f"Redaction left a known value in the text ({len(term)} characters).")
    leftover = _pattern_spans(text)
    if leftover:
        raise LeakError(f"Redaction left a {leftover[0].kind} pattern in the text.")


# --- layers -------------------------------------------------------------------------------


def _customer_terms(c: Customer) -> list[tuple[str, tuple[str, str, str]]]:
    """(search term, (kind, key, canonical value)) for everything we know about a customer."""
    person = ("PERSON", f"cust:{c.id}:name", c.name)
    terms = [(c.name, person)]
    parts = c.name.split()
    if len(parts) > 1:
        terms += [(p, person) for p in (parts[0], parts[-1]) if len(p) >= 3]
    terms += [(a, person) for a in c.aliases]
    if c.company:
        terms.append((c.company, ("FIRMA", f"cust:{c.id}:company", c.company)))
    if c.address:
        addr = ("ADRESSE", f"cust:{c.id}:address", c.address)
        terms.append((c.address, addr))
        terms += [(p.strip(), addr) for p in c.address.split(",") if len(p.strip()) >= 4]
    if c.email:
        terms.append((c.email, ("EMAIL", f"cust:{c.id}:email", c.email)))
    if c.phone:
        terms.append((c.phone, ("TEL", f"cust:{c.id}:phone", c.phone)))
    return terms


def _word_regex(term: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!{_W}){body}(?:s|'s)?(?!{_W})", re.IGNORECASE)


def _customer_spans(text: str, customers: list[Customer]) -> list[Span]:
    spans = []
    for customer in customers:
        for term, (kind, key, canonical) in _customer_terms(customer):
            if len(term.strip()) < 3:
                continue
            for m in _word_regex(term).finditer(text):
                spans.append(Span(m.start(), m.end(), kind, key, canonical, CUSTOMER))
    return spans


def _pattern_spans(text: str) -> list[Span]:
    spans = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if _PLACEHOLDER.fullmatch(m.group()):
                continue
            spans.append(_value_span(m.start(), m.end(), kind, text, PATTERN))
    for m in _BIRTHDATE.finditer(text):
        spans.append(_value_span(m.start(1), m.end(1), "GEBDATUM", text, PATTERN))
    for m in _STEUER_ID.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        before = text[max(0, m.start() - 40) : m.start()]
        if _steuer_id_valid(digits) or _STEUER_ID_LABEL.search(before):
            spans.append(_value_span(m.start(), m.end(), "STEUERID", text, PATTERN))
    for m in _SVNR.finditer(text):
        spans.append(_value_span(m.start(), m.end(), "SVNR", text, PATTERN))
    spans.extend(_iban_spans(text))
    return spans


def _steuer_id_valid(digits: str) -> bool:
    """Check digit of the Steuer-ID (ISO 7064 MOD 11,10, as § 139b AO uses it)."""
    if len(digits) != 11 or digits[0] == "0":
        return False
    # In the first ten digits exactly one digit repeats (twice or three times).
    counts = sorted((digits[:10].count(d) for d in set(digits[:10])), reverse=True)
    if counts[0] not in (2, 3) or (len(counts) > 1 and counts[1] > 1):
        return False
    product = 10
    for d in digits[:10]:
        total = (int(d) + product) % 10 or 10
        product = (total * 2) % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(digits[10])


def _iban_spans(text: str) -> list[Span]:
    spans = []
    for m in _IBAN_START.finditer(text):
        chars, ends, i = [], [], m.start()
        while i < len(text) and len(chars) < 34:
            ch = text[i]
            if ch.isascii() and (ch.isdigit() or ch.isupper()):
                chars.append(ch)
                ends.append(i + 1)
            elif ch not in " \t\n" or (ch == "\n" and text[i - 1] == "\n"):
                break
            i += 1
        for length in range(len(chars), 14, -1):
            if _iban_valid("".join(chars[:length])):
                spans.append(_value_span(m.start(), ends[length - 1], "IBAN", text, PATTERN))
                break
    return spans


def _iban_valid(iban: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _salutation_spans(text: str) -> list[Span]:
    spans = []
    for m in _SALUTATION.finditer(text):
        group = 1 if m.group(1) else 2
        spans.append(_value_span(m.start(group), m.end(group), "PERSON", text, SALUTATION))
    return spans


def _ner_spans(text: str, nlp) -> list[Span]:
    """Entities as tagged by spaCy, trimmed to their proper-noun (NE) tokens.

    German capitalizes every noun, so the model often tags things like "Lampe" or
    "Backofen-Abzug" as names. Keeping only runs of proper-noun tokens drops those while
    names and places survive. All-caps single words are receipt headers, not names.
    """
    kinds = {"PER": "PERSON", "ORG": "FIRMA", "LOC": "ORT"}
    spans = []
    for ent in nlp(text).ents:
        kind = kinds.get(ent.label_)
        if kind is None:
            continue
        run: list = []
        for token in [*ent, None]:
            if token is not None and token.tag_ == "NE" and "\n" not in token.whitespace_:
                run.append(token)
                continue
            if token is not None and token.tag_ == "NE":
                run.append(token)
            if run:
                span = _ner_run(run, kind, text)
                if span:
                    spans.append(span)
            run = []
    return spans


def _ner_run(tokens: list, kind: str, text: str) -> Span | None:
    start, end = tokens[0].idx, tokens[-1].idx + len(tokens[-1].text)
    value = text[start:end]
    if len(value) < 3 or value.lower() in _NER_STOPWORDS or _PLACEHOLDER.search(value):
        return None
    if len(tokens) == 1 and value.isupper():
        return None
    if kind == "ORT" and value.lower() in _LOCATION_ALLOW:
        return None
    return _value_span(start, end, kind, text, NER)


def _value_span(start: int, end: int, kind: str, text: str, layer: int) -> Span:
    value = text[start:end]
    key = f"{kind}:{re.sub(r'\s+', ' ', value).lower()}"
    if kind == "IBAN":
        key = "IBAN:" + re.sub(r"\s+", "", value)
    return Span(start, end, kind, key, re.sub(r"\s+", " ", value), layer)


def _resolve(spans: list[Span]) -> list[Span]:
    """Keep non-overlapping spans, preferring earlier layers and then longer matches."""
    taken: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.layer, -(s.end - s.start), s.start)):
        if all(span.end <= t.start or span.start >= t.end for t in taken):
            taken.append(span)
    return sorted(taken, key=lambda s: s.start)
