"""Customers are Markdown notes with YAML frontmatter, so they open in Obsidian or any text editor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


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

    @property
    def display_name(self) -> str:
        return self.company or self.name


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
    )


def load_customers(folder: Path) -> list[Customer]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    customers = (parse_note(p) for p in sorted(folder.glob("*.md")))
    return [c for c in customers if c is not None]


def _opt(value: object) -> str | None:
    return str(value).strip() if value not in (None, "") else None
