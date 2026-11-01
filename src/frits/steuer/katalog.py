"""Der Fragebogen und der Katalog: welche Provinzen es gibt und was dort gesammelt wird.

Der Katalog liegt in katalog.toml, damit man ihn ohne Programmieren ergänzen kann.
Aus den Antworten im Fragebogen (Profil) wird die Liste der Provinzen für ein Steuerjahr.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

import tomli_w


@dataclass(frozen=True)
class Stueck:
    key: str
    name: str
    hinweis: str = ""


@dataclass(frozen=True)
class Provinz:
    key: str
    name: str  # Spielname, z. B. "Gehöft"
    bereich: str  # Klartext, z. B. "Vermietung"
    frage: str  # welche Antwort im Fragebogen diese Provinz freischaltet
    stuecke: tuple[Stueck, ...]
    woerter: tuple[str, ...] = ()
    pro_einheit: bool = False


@cache
def katalog() -> dict[str, Provinz]:
    raw = tomllib.loads(
        (resources.files("frits.steuer") / "katalog.toml").read_text(encoding="utf-8")
    )
    result = {}
    for p in raw["provinz"]:
        result[p["key"]] = Provinz(
            key=p["key"],
            name=p["name"],
            bereich=p["bereich"],
            frage=p["frage"],
            stuecke=tuple(Stueck(**s) for s in p["stuecke"]),
            woerter=tuple(p.get("woerter", ())),
            pro_einheit=p.get("pro_einheit", False),
        )
    return result


@dataclass
class Profil:
    """Die Antworten aus dem Fragebogen. Gilt für dich, Fritz und alle anderen."""

    jahr: int
    selbststaendig: bool = False
    gesellschaft: bool = False
    angestellt: bool = False
    wohnungen: int = 0
    depot_inland: bool = False
    depot_ausland: bool = False
    familie: bool = False
    umsatzsteuer: str = "keine"  # keine | monatlich | quartal
    beraterin_email: str = ""
    name: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def antwort(self, frage: str) -> int:
        """Wie oft eine Provinz vorkommt: 0 = gar nicht."""
        if frage == "immer":
            return 1
        if frage == "vermietung":
            return max(0, self.wohnungen)
        return 1 if getattr(self, frage, False) else 0

    @classmethod
    def load(cls, path: Path) -> Profil | None:
        if not Path(path).exists():
            return None
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(tomli_w.dumps(asdict(self)), encoding="utf-8")


@dataclass(frozen=True)
class Gebiet:
    """Eine Provinz im eigenen Reich, z. B. "Gehöft 2"."""

    id: str  # "gehoeft-2"
    provinz: Provinz
    nummer: int = 0  # 0 = gibt es nur einmal

    @property
    def titel(self) -> str:
        return f"{self.provinz.name} {self.nummer}" if self.nummer else self.provinz.name

    @property
    def bereich(self) -> str:
        return (
            f"{self.provinz.bereich} · Nr. {self.nummer}" if self.nummer else self.provinz.bereich
        )


def reich(profil: Profil) -> list[Gebiet]:
    """Alle Gebiete, die der Fragebogen freischaltet, in fester Reihenfolge."""
    gebiete = []
    for provinz in katalog().values():
        anzahl = profil.antwort(provinz.frage)
        if not anzahl:
            continue
        if provinz.pro_einheit:
            gebiete += [Gebiet(f"{provinz.key}-{n}", provinz, n) for n in range(1, anzahl + 1)]
        else:
            gebiete.append(Gebiet(provinz.key, provinz))
    return gebiete


def gebiet(profil: Profil, gebiet_id: str) -> Gebiet | None:
    return next((g for g in reich(profil) if g.id == gebiet_id), None)
