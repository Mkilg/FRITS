"""Der Fragebogen und der Katalog: welche Provinzen es gibt und was dort gesammelt wird.

Der Katalog liegt in katalog.toml, damit man ihn ohne Programmieren ergänzen kann.
Aus den Antworten im Fragebogen (Profil) wird die Liste der Gebiete für ein Steuerjahr.
Das passt für jede Lebenslage: nur Gehalt, mehrere Firmen, Wohnungen und Büros,
verheiratet mit gemeinsamer Erklärung oder allein.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

import tomli_w

PFLICHT = "pflicht"
BONUS = "bonus"

FAMILIENSTAND = {
    "ledig": "ledig",
    "verheiratet": "verheiratet oder eingetragene Lebenspartnerschaft",
    "getrennt": "getrennt lebend",
    "geschieden": "geschieden",
    "verwitwet": "verwitwet",
}

# Teil 3 des Fragebogens: Ausgaben, die Steuern sparen können (Bonus-Schätze),
# und ein paar Lebenslagen, die einzelne Sammelstücke freischalten.
HAKEN = {
    "vorsorge": "Versicherungen und Altersvorsorge (Kranken-, Renten-, Haftpflichtversicherung)",
    "spenden": "Spenden oder Mitgliedsbeiträge",
    "haushalt": "Handwerker, Putz- oder Gartenhilfe im eigenen Haushalt",
    "sanierung": "Energetische Sanierung am selbst bewohnten Haus",
    "krankheit": "Hohe Krankheitskosten (Zahnersatz, Brille, Therapie)",
    "pflege": "Pflege, Pflegegrad oder Behinderung",
    "unterhalt": "Unterhalt an Angehörige gezahlt",
    "umzug": "Beruflich bedingter Umzug",
    "ksk": "Über die Künstlersozialkasse (KSK) versichert",
    "sender": "Honorare von Rundfunksendern (ARD, ZDF, Deutschlandradio …)",
    "verwertung": "Geld von VG Wort, VG Bild-Kunst oder GVL",
    "ausland_honorar": "Honorare von Auftraggebern im Ausland",
    "verlust": "Verlust im Vorjahr (Verlustvortrag)",
    "arbeit": "Kosten für die Arbeit als Angestellte:r (Laptop, Fahrten, Fortbildung)",
    "steuerberatung": "Rechnung der Steuerberatung vom letzten Jahr",
    "kirche": "Kirchenmitglied",
    "kinder_betreuung": "Kinderbetreuung (Kita, Tagesmutter, Hort)",
    "kinder_schulgeld": "Schulgeld für eine Privatschule",
    "kinder_ausbildung": "Kind über 18 in Ausbildung oder Studium",
}

# Manche Haken passen nur, wenn es die Einkunft gibt: Haken -> Fragen aus Teil 2.
HAKEN_NUR_MIT = {
    "arbeit": ("angestellt",),
    "umzug": ("angestellt", "freiberuflich"),
    "ksk": ("freiberuflich",),
    "sender": ("freiberuflich",),
    "verwertung": ("freiberuflich",),
    "ausland_honorar": ("freiberuflich", "gewerbe"),
    "verlust": ("freiberuflich", "gewerbe"),
}


@dataclass(frozen=True)
class Stueck:
    key: str
    name: str
    hinweis: str = ""
    art: str = PFLICHT  # pflicht | bonus
    wenn: str = ""  # nur dabei, wenn dieser Haken im Fragebogen gesetzt ist
    kern: bool = False  # gibt es fast immer: "Gibt's nicht" fragt einmal nach
    fehlt_nie: bool = False  # hat jede:r (Steuer-ID): statt "Gibt's nicht" nur "Finde ich nicht"
    warum: str = ""  # warum es das fast immer gibt: steht in der Rückfrage bei "Gibt's nicht"


@dataclass(frozen=True)
class Provinz:
    key: str
    name: str  # Spielname, z. B. "Gehöft"
    bereich: str  # Klartext, z. B. "Vermietete Wohnung"
    frage: str  # welche Antwort im Fragebogen diese Provinz freischaltet
    stuecke: tuple[Stueck, ...]
    woerter: tuple[str, ...] = ()
    zaehlbar: bool = False  # eine Provinz pro Stück (Wohnung, Arbeitgeber, Firma ...)
    person: bool = False  # gehört einer Person, nicht beiden
    anlage: str = ""


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
            zaehlbar=p.get("zaehlbar", False),
            person=p.get("person", False),
            anlage=p.get("anlage", ""),
        )
    return result


def fragen() -> list[Provinz]:
    """Die Provinzen, nach denen Teil 2 des Fragebogens fragt (ohne die immer dabei sind)."""
    return [p for p in katalog().values() if p.frage not in ("immer", "kinder")]


@dataclass
class Profil:
    """Die Antworten aus dem Fragebogen."""

    jahr: int
    name: str = ""
    partner: str = ""
    familienstand: str = "ledig"
    zusammen: bool = False  # gemeinsame Erklärung mit Ehe- oder Lebenspartner:in
    kinder: int = 0
    mengen: dict[str, list[int]] = field(default_factory=dict)  # frage -> [du, partner]
    haken: list[str] = field(default_factory=list)
    umsatzsteuer: str = "keine"  # keine | monatlich | quartal
    beraterin_email: str = ""
    beraterin_termin: str = ""

    @property
    def personen(self) -> list[str]:
        ich = self.name or "Du"
        return [ich, self.partner or "Partner:in"] if self.zusammen else [ich]

    def anzahl(self, frage: str) -> list[int]:
        """Wie viele Provinzen pro Person (bei gemeinsamer Erklärung zwei Zahlen)."""
        if frage == "immer":
            return [1]
        if frage == "kinder":
            return [1 if self.kinder > 0 else 0]
        werte = list(self.mengen.get(frage, []))
        werte += [0] * (2 - len(werte))
        return [max(0, int(werte[0])), max(0, int(werte[1])) if self.zusammen else 0]

    def zeigt_haken(self, haken: str) -> bool:
        """Ob die Frage nach diesem Haken im Fragebogen überhaupt Sinn ergibt."""
        if haken.startswith("kinder_"):
            return bool(self.kinder)
        noetig = HAKEN_NUR_MIT.get(haken)
        return not noetig or any(sum(self.anzahl(f)) for f in noetig)

    def hat(self, haken: str) -> bool:
        if haken == "umsatzsteuer":
            return self.umsatzsteuer != "keine"
        return haken in self.haken and self.zeigt_haken(haken)

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
    """Eine Provinz im eigenen Reich, z. B. "Gehöft 2" oder "Kaserne (Alex)"."""

    id: str  # "gehoeft-2", "kaserne-p2-1"
    provinz: Provinz
    nummer: int = 0  # 0 = gibt es nur einmal
    person: int | None = None  # 0 = du, 1 = Partner:in, None = gemeinsam
    wappen: str = ""  # Name der Person, wenn es zwei gibt
    stuecke: tuple[Stueck, ...] = ()
    zeige_nummer: bool = False

    @property
    def titel(self) -> str:
        titel = self.provinz.name
        if self.zeige_nummer:
            titel += f" {self.nummer}"
        if self.wappen:
            titel += f" ({self.wappen})"
        return titel

    @property
    def bereich(self) -> str:
        teile = [self.provinz.bereich]
        if self.zeige_nummer:
            teile.append(f"Nr. {self.nummer}")
        if self.wappen:
            teile.append(self.wappen)
        return " · ".join(teile)


def reich(profil: Profil) -> list[Gebiet]:
    """Alle Gebiete, die der Fragebogen freischaltet, in fester Reihenfolge."""
    gebiete = []
    zwei = profil.zusammen
    for provinz in katalog().values():
        stuecke = tuple(s for s in provinz.stuecke if not s.wenn or profil.hat(s.wenn))
        if not stuecke:
            continue
        anzahl = profil.anzahl(provinz.frage)
        if not provinz.person:
            anzahl = [sum(anzahl)]
        for p, wie_viele in enumerate(anzahl):
            if not provinz.zaehlbar:
                wie_viele = min(wie_viele, 1)
            for n in range(1, wie_viele + 1):
                gid = provinz.key
                if p == 1:
                    gid += "-p2"
                if provinz.zaehlbar:
                    gid += f"-{n}"
                gebiete.append(
                    Gebiet(
                        id=gid,
                        provinz=provinz,
                        nummer=n if provinz.zaehlbar else 0,
                        person=p if provinz.person else None,
                        wappen=profil.personen[p] if provinz.person and zwei else "",
                        stuecke=stuecke,
                        zeige_nummer=provinz.zaehlbar and wie_viele > 1,
                    )
                )
    return gebiete


def ist_kern(gebiet_id: str, key: str) -> bool:
    """Gibt es dieses Sammelstück fast immer (z. B. Belege bei Selbstständigen)?"""
    provinz = katalog().get(gebiet_id.split("-")[0])
    return bool(provinz) and any(s.key == key and s.kern for s in provinz.stuecke)


def fehlt_nie(gebiet_id: str, key: str) -> bool:
    """Gibt es dieses Stück bei allen (z. B. die Steuer-ID)? Dann ist "Gibt's nicht" falsch."""
    provinz = katalog().get(gebiet_id.split("-")[0])
    return bool(provinz) and any(s.key == key and s.fehlt_nie for s in provinz.stuecke)


def warum(gebiet_id: str, key: str) -> str:
    """Der Grund, warum es dieses wichtige Stück fast immer gibt ("" wenn keiner hinterlegt ist)."""
    provinz = katalog().get(gebiet_id.split("-")[0])
    if not provinz:
        return ""
    return next((s.warum for s in provinz.stuecke if s.key == key), "")


def gebiet(profil: Profil, gebiet_id: str) -> Gebiet | None:
    return next((g for g in reich(profil) if g.id == gebiet_id), None)
