"""Check what the AI returned against the document itself. The model suggests; code decides."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from frits.redact import Mapping
from frits.schema import ExtractionResult, Item

# Upper bounds per line; anything above becomes a question for Fritz instead of a charge.
MAX_QUANTITY = {"Std": 24, "km": 2000, "Stk": 500, "pauschal": 1}

# Phrases that try to talk to the AI. Their presence is shown to Fritz as a teaching moment.
_INJECTION = re.compile(
    r"(?i)ignorier\w*\s+(alle|die|deine|bisherige)|ignore\s+(all|previous|your)|"
    r"\b(system|admin)\s*(:|mode|modus)|für die ki\b|for the (ai|model)\b|you are now"
)


@dataclass
class CheckedItem:
    item: Item
    flags: list[str] = field(default_factory=list)


@dataclass
class CheckedExtraction:
    doc_type: str
    customer_ref: str | None
    items: list[CheckedItem]
    questions: list[str]
    injection_suspected: bool


def check(result: ExtractionResult, redacted_text: str, mapping: Mapping) -> CheckedExtraction:
    questions = list(result.questions)
    haystack = _normalize(redacted_text)

    customer_ref = result.customer_ref
    if customer_ref is not None and customer_ref not in mapping.entries:
        questions.append(
            "Für welchen Kunden war diese Arbeit? Die KI konnte es nicht sicher sagen."
        )
        customer_ref = None

    items = []
    for item in result.items:
        quote = _normalize(item.source_quote)
        if not quote or quote not in haystack:
            questions.append(
                f"Die KI hat „{item.description}“ vorgeschlagen, das steht so aber nicht im Dokument. "
                "Diese Position wurde weggelassen."
            )
            continue
        checked = CheckedItem(item)
        if item.stated_price is not None and not _price_in_text(item.stated_price, redacted_text):
            checked.item = item.model_copy(update={"stated_price": None})
            checked.flags.append("price_not_in_text")
        if item.quantity > MAX_QUANTITY[item.unit]:
            checked.flags.append("quantity_unusual")
            questions.append(
                f"Stimmt die Menge bei „{item.description}“ ({item.quantity:g} {item.unit})?"
            )
        items.append(checked)

    return CheckedExtraction(
        doc_type=result.doc_type,
        customer_ref=customer_ref,
        items=items,
        questions=questions,
        injection_suspected=bool(_INJECTION.search(redacted_text)),
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _price_in_text(price: float, text: str) -> bool:
    digits = re.sub(r"[^\d,.]", " ", text)
    for token in digits.split():
        cleaned = token.strip(".,").replace(".", "").replace(",", ".")
        try:
            if abs(float(cleaned) - price) < 0.005:
                return True
        except ValueError:
            continue
    return False
