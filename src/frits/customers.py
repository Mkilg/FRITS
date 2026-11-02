"""Customers are Markdown notes with YAML frontmatter, so they open in Obsidian or any text editor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


# Mail providers anyone can sign up with: an address there says nothing about an employer.
_FREEMAIL = {
    "example",  # example.org/.com/.net are reserved for examples and tests
    "gmail",
    "googlemail",
    "web",
    "gmx",
    "t-online",
    "yahoo",
    "outlook",
    "hotmail",
    "live",
    "icloud",
    "me",
    "mac",
    "posteo",
    "mailbox",
    "freenet",
    "aol",
    "proton",
    "protonmail",
    "arcor",
    "online",
    "email",
    "mail",
    "kabelmail",
    "vodafone",
    "tutanota",
    "tuta",
    "ymail",
}


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    company: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    hourly_rate: Decimal | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    umsatzsteuer: str = ""  # "" Germany, "eu" another EU country, "drittland" outside the EU
    ust_id: str | None = None
    land: str = "DE"  # ISO 3166 country code
    referenz: str | None = None  # Leitweg-ID or order number to quote on every invoice
    privat: bool = False  # a private person, not a business: German VAT rules stay
    gutschrift: bool = False  # the customer bills itself (Gutschrift), Fritz writes no invoice

    @property
    def display_name(self) -> str:
        return self.company or self.name

    @property
    def firmen_domain(self) -> str | None:
        """The e-mail's own domain when there is no company: then the invoice may be meant for
        the organisation behind it (jana.weber@rbb-online.de), not for the person."""
        if self.company or self.privat or not self.email or "@" not in self.email:
            return None
        domain = self.email.rsplit("@", 1)[1].strip().lower()
        if not domain or domain.split(".")[0] in _FREEMAIL:
            return None
        return domain


def parse_note(path: Path) -> Customer | None:
    """Return the customer described by a note, or None if the note isn't a customer note."""
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict) or meta.get("type") != "kunde" or not meta.get("name"):
        return None
    rate = meta.get("hourly_rate")
    return Customer(
        id=Path(path).stem,
        name=str(meta["name"]).strip(),
        company=_opt(meta.get("company")),
        address=_opt(meta.get("address")),
        email=_opt(meta.get("email")),
        phone=_opt(meta.get("phone")),
        hourly_rate=Decimal(str(rate)) if rate is not None else None,
        aliases=tuple(str(a).strip() for a in meta.get("aliases") or ()),
        umsatzsteuer=_where(meta.get("umsatzsteuer")),
        ust_id=_opt(meta.get("ust_id")),
        land=_country(meta.get("land")),
        referenz=_opt(meta.get("referenz")),
        privat=bool(meta.get("privat")),
        gutschrift=bool(meta.get("gutschrift")),
    )


def load_customers(folder: Path) -> list[Customer]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    customers = (parse_note(p) for p in sorted(folder.glob("*.md")))
    return [c for c in customers if c is not None]


def note_id(name: str) -> str:
    """File name for a new customer note: "Bäckerei Sonnenschein" -> "baeckerei-sonnenschein"."""
    text = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "kunde"


def save_note(folder: Path, customer: Customer) -> Path:
    """Write the customer's frontmatter and keep whatever Fritz wrote below it."""
    path = Path(folder) / f"{customer.id}.md"
    body = "\n# Notizen\n"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(text)
        body = text[match.end() :] if match else text
        if not body.startswith("\n"):
            body = "\n" + body
    meta = {"type": "kunde", "name": customer.name}
    for key in ("company", "address", "email", "phone"):
        if getattr(customer, key):
            meta[key] = getattr(customer, key)
    if customer.hourly_rate is not None:
        meta["hourly_rate"] = float(customer.hourly_rate)
    if customer.aliases:
        meta["aliases"] = list(customer.aliases)
    if customer.umsatzsteuer:
        meta["umsatzsteuer"] = customer.umsatzsteuer
    if customer.ust_id:
        meta["ust_id"] = customer.ust_id
    if customer.land and customer.land != "DE":
        meta["land"] = customer.land
    if customer.referenz:
        meta["referenz"] = customer.referenz
    if customer.privat:
        meta["privat"] = True
    if customer.gutschrift:
        meta["gutschrift"] = True
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return path


# Countries Fritz is likely to bill, for the customer form. Notes may hold any ISO code.
COUNTRIES = {
    "DE": "Deutschland",
    "AT": "Österreich",
    "CH": "Schweiz",
    "LU": "Luxemburg",
    "NL": "Niederlande",
    "BE": "Belgien",
    "FR": "Frankreich",
    "IT": "Italien",
    "ES": "Spanien",
    "DK": "Dänemark",
    "SE": "Schweden",
    "PL": "Polen",
    "CZ": "Tschechien",
    "IE": "Irland",
    "GB": "Vereinigtes Königreich",
    "NO": "Norwegen",
    "US": "USA",
    "FI": "Finnland",
    "PT": "Portugal",
    "LI": "Liechtenstein",
}


EU_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
}  # without DE


def vat_place(land: str, privat: bool) -> str:
    """Where the customer sits for VAT, from the country: "" Germany, "eu" or "drittland".

    Private persons keep "": for them German VAT is the normal case (§ 3a Abs. 1 UStG),
    and the exceptions are a question for the tax adviser."""
    if privat or land == "DE":
        return ""
    return "eu" if land in EU_COUNTRIES else "drittland"


def _country(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"[A-Z]{2}", text) else "DE"


def _where(value: object) -> str:
    """Only the values FRITS knows; anything else means Germany."""
    text = str(value or "").strip().lower()
    return text if text in ("eu", "drittland") else ""


def _opt(value: object) -> str | None:
    return str(value).strip() if value not in (None, "") else None
