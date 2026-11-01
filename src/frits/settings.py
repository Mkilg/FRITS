"""Where FRITS keeps its files, and Fritz's business settings (settings.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

import tomli_w

from frits.customers import Customer

FOLDERS = (
    "inbox",
    "privat",
    "check-me",
    "done",
    "invoices",
    "quotes",
    "customers",
    ".mappings",
)


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def default(cls) -> Workspace:
        home = os.environ.get("FRITS_HOME")
        return cls(Path(home).expanduser() if home else Path.home() / "Documents" / "FRITS")

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def check_me(self) -> Path:
        return self.root / "check-me"

    @property
    def done(self) -> Path:
        return self.root / "done"

    @property
    def invoices(self) -> Path:
        return self.root / "invoices"

    @property
    def quotes(self) -> Path:
        return self.root / "quotes"

    @property
    def customers(self) -> Path:
        return self.root / "customers"

    @property
    def mappings(self) -> Path:
        return self.root / ".mappings"

    @property
    def db(self) -> Path:
        return self.root / "frits.db"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.toml"

    def ensure(self) -> Workspace:
        for folder in FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class PriceItem:
    """One entry of Fritz's price list, e.g. "Fenster putzen", "Stk", 25."""

    name: str
    unit: str
    price: Decimal

    def matches(self, description: str, unit: str) -> bool:
        if unit != self.unit:
            return False
        # Compare word beginnings, so "Fenster putzen" also finds "3 Fenster geputzt".
        stems = [w[:4] if len(w) > 4 else w for w in self.name.casefold().split() if len(w) > 2]
        text = description.casefold()
        return bool(stems) and all(stem in text for stem in stems)


@dataclass
class Settings:
    # Who writes the invoices
    name: str = ""
    company: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    steuernummer: str = ""
    ust_id: str = ""
    bank: str = ""
    iban: str = ""
    bic: str = ""
    # Tax and prices
    tax_mode: str = "kleinunternehmer"  # or "regelbesteuerung"
    vat_rate: Decimal = Decimal(19)
    hourly_rate: Decimal = Decimal(0)
    km_rate: Decimal = Decimal("0.30")
    payment_days: int = 14
    # Invoice numbers: JJJJ = year, JJ = two-digit year, NNNN = counter with leading zeros
    number_pattern: str = "JJJJ-NNNN"
    last_number_before: str = ""  # the last number Fritz wrote before FRITS, e.g. "2026-0042"
    # Price list for anything that isn't hours or km: [{"name": ..., "unit": ..., "price": ...}]
    prices: list[dict] = field(default_factory=list)
    # AI
    provider: str = "claude"  # claude | openai | openrouter | ollama
    models: dict[str, str] = field(default_factory=dict)
    strict_local: bool = False
    # Calm extras
    sound: bool = False  # cues and focus music; off until Fritz turns it on
    music: str = "entspannt"  # music mood, see frits.sounds.MOODS
    effects: str = "ruhig"  # "ruhig" (stamp only) or "verspielt" (numbers tick up, sparkle)
    haustier: bool = True  # Fritte's room and shop on the Heute page
    setup_done: bool = False

    @property
    def owner(self) -> Customer:
        """Fritz's own details, redacted like a customer's: they identify him too."""
        return Customer(
            id="owner",
            name=self.name,
            company=self.company or None,
            address=self.address or None,
            email=self.email or None,
            phone=self.phone or None,
        )

    @property
    def price_list(self) -> list[PriceItem]:
        items = []
        for entry in self.prices:
            try:
                price = Decimal(str(entry.get("price", "")))
            except ArithmeticError:
                continue
            name = str(entry.get("name", "")).strip()
            if name and price >= 0:
                items.append(PriceItem(name, str(entry.get("unit") or "Stk"), price))
        return items

    @property
    def is_complete(self) -> bool:
        has_prices = self.hourly_rate > 0 or bool(self.price_list)
        return bool(self.name and self.address and self.iban and has_prices)

    @classmethod
    def load(cls, path: Path) -> Settings:
        path = Path(path)
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        known = cls.__dataclass_fields__
        values = {}
        for key, value in data.items():
            if key not in known:
                continue
            if known[key].type == "Decimal":
                value = Decimal(str(value))
            values[key] = value
        return cls(**values)

    def save(self, path: Path) -> None:
        data = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(tomli_w.dumps(data), encoding="utf-8")
