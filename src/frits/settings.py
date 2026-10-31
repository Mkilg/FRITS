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
    # AI
    provider: str = "claude"  # claude | openai | openrouter | ollama
    models: dict[str, str] = field(default_factory=dict)
    strict_local: bool = False
    # Calm extras
    sound: bool = False  # cues and focus music; off until Fritz turns it on
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
    def is_complete(self) -> bool:
        return bool(self.name and self.address and self.iban and self.hourly_rate > 0)

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
