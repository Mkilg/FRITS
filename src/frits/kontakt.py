"""Turn a pasted e-mail signature, a business card or a dragged contact (.vcf) into the fields
of a new customer. Everything happens on this computer, without AI; Fritz checks the fields."""

from __future__ import annotations

import re

_COMPANY = re.compile(
    r"^.{2,60}?\b(GmbH(?: & Co\.? KG)?|AG|UG(?: \(haftungsbeschränkt\))?|KG|OHG|GbR|e\.\s?K\.|"
    r"e\.\s?V\.|mbH|Ltd\.?|Inc\.?|PartG)(?=\W|$)",
    re.MULTILINE,
)
_HONORIFIC = re.compile(r"^(Herr|Frau|Hr\.|Fr\.|Dr\.|Prof\.)\s+", re.IGNORECASE)


def _unfold(vcard: str) -> list[str]:
    """vCard lines may continue on the next line when it starts with a space."""
    lines: list[str] = []
    for raw in vcard.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw.strip())
    return lines


def from_vcard(vcard: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in _unfold(vcard):
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        key = head.split(";", 1)[0].upper().split(".")[-1]  # "item1.EMAIL" -> "EMAIL"
        value = value.replace("\\,", ",").replace("\\n", " ").replace("\\;", ";").strip()
        if not value:
            continue
        if key == "FN":
            found.setdefault("name", value)
        elif key == "N" and "name" not in found:
            parts = value.split(";")
            found["name"] = " ".join(p for p in (parts[1:2] + parts[:1]) if p).strip()
        elif key == "ORG":
            found.setdefault("company", value.split(";")[0].strip())
        elif key == "EMAIL":
            found.setdefault("email", value)
        elif key == "TEL":
            found.setdefault("phone", value)
        elif key == "ADR":
            parts = [p.strip() for p in value.split(";")] + [""] * 7
            street, town, zip_code = parts[2], parts[3], parts[5]
            place = " ".join(p for p in (zip_code, town) if p)
            address = ", ".join(p for p in (street, place) if p)
            if address:
                found.setdefault("address", address)
    if found.get("company") and found.get("name") == found["company"]:
        del found["name"]  # a company card without a person
    return found


def details(text: str, nlp=None) -> dict[str, str]:
    """Name, company, address, e-mail and phone found in `text`."""
    if "BEGIN:VCARD" in text.upper():
        return from_vcard(text)
    from frits.setup import own_details

    found = {
        key: value
        for key, value in own_details(text, nlp).items()
        if key in ("name", "address", "email", "phone")
    }
    if "name" in found:  # the language model sometimes runs on into the next line
        found["name"] = found["name"].splitlines()[0].strip()
    if m := _COMPANY.search(text):
        found["company"] = m.group(0).strip(" ,|")
    if "name" not in found:
        for line in text.splitlines():
            line = line.strip(" -,")
            if _HONORIFIC.match(line) and len(line.split()) <= 5:
                found["name"] = line
                break
    return found
