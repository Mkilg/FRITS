"""Die Spielregeln der Steuer-Quest. Reine Rechnerei, ohne Datenbank und ohne Dateien.

Ideen aus Spielen, die das gut machen:
- Dorfromantik: Jede Provinz wächst sichtbar mit jedem Dokument (Stufe 0 bis 3).
- Balatro: Mehrere Rückmeldungen auf einmal, und Serien werden immer lauter (Combo).
- Hades: Ein Bosskampf, der fair ist, und bei dem nichts verloren geht.

Wichtig: Die Regeln belohnen, sie bestrafen nie. Es gibt keine Fristen-Strafen,
keine verlorenen Punkte und immer einen Notausgang.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from frits.steuer.katalog import Gebiet, ist_kern

ERLEDIGT = ("gesammelt", "gibts_nicht", "fragen")

ZEITALTER = (
    (0.0, "Zeitalter des Chaos", "Papierstapel überall. Jedes Dokument bringt Ordnung."),
    (0.4, "Zeitalter der Ordnung", "Dein Reich nimmt Form an."),
    (1.0, "Goldenes Zeitalter", "Alles beisammen. Der Drache hat keine Karten mehr."),
)

STUFEN = ("im Nebel", "bebaut", "belebt", "blühend")


@dataclass(frozen=True)
class Stand:
    """Wie weit eine Provinz oder das ganze Reich ist."""

    gesamt: int
    gesammelt: int
    gibts_nicht: int
    fragen: int

    @property
    def erledigt(self) -> int:
        return self.gesammelt + self.gibts_nicht + self.fragen

    @property
    def offen(self) -> int:
        return self.gesamt - self.erledigt

    @property
    def anteil(self) -> float:
        return self.erledigt / self.gesamt if self.gesamt else 1.0

    @property
    def prozent(self) -> int:
        return round(self.anteil * 100)

    @property
    def stufe(self) -> int:
        """0 = im Nebel, 3 = blühend. Stufe 3 gibt es nur, wenn nichts mehr offen ist."""
        if self.gesamt and self.offen == 0:
            return 3
        return min(2, int(self.anteil * 3))

    @property
    def siegel(self) -> bool:
        return self.gesamt > 0 and self.offen == 0


def ist_bonus(s: dict) -> bool:
    return s.get("art", "pflicht") == "bonus"


def pflicht(stuecke: list[dict]) -> list[dict]:
    """Nur die Pflicht-Stücke. Bonus-Schätze zählen nie gegen dich."""
    return [s for s in stuecke if not ist_bonus(s)]


def stand(stuecke: list[dict]) -> Stand:
    return Stand(
        gesamt=len(stuecke),
        gesammelt=sum(1 for s in stuecke if s["status"] == "gesammelt"),
        gibts_nicht=sum(1 for s in stuecke if s["status"] == "gibts_nicht"),
        fragen=sum(1 for s in stuecke if s["status"] == "fragen"),
    )


@dataclass(frozen=True)
class Provinzstand:
    gebiet: Gebiet
    stand: Stand  # über die Pflicht-Stücke (oder die Schätze, wenn es nur Schätze gibt)
    stuecke: list[dict]

    @property
    def stufe_name(self) -> str:
        return STUFEN[self.stand.stufe]

    @property
    def nur_bonus(self) -> bool:
        """Eine Provinz nur aus Schätzen (z. B. die Burg) ist Extra, nie Pflicht."""
        return bool(self.stuecke) and all(ist_bonus(s) for s in self.stuecke)

    @property
    def schaetze(self) -> int:
        """Gefundene Bonus-Schätze."""
        return sum(1 for s in self.stuecke if ist_bonus(s) and s["status"] == "gesammelt")


def provinzen(gebiete: list[Gebiet], stuecke: list[dict]) -> list[Provinzstand]:
    result = []
    for g in gebiete:
        eigene = [s for s in stuecke if s["gebiet"] == g.id]
        basis = pflicht(eigene) or eigene
        result.append(Provinzstand(g, stand(basis), eigene))
    return result


def gesamtstand(stuecke: list[dict]) -> Stand:
    """Der Fortschritt des ganzen Reichs: nur Pflicht-Stücke zählen."""
    return stand(pflicht(stuecke))


def zeitalter(anteil: float) -> tuple[int, str, str]:
    """(Nummer, Name, Satz) des aktuellen Zeitalters."""
    nummer = 0
    for i, (ab, _name, _satz) in enumerate(ZEITALTER):
        if anteil >= ab:
            nummer = i
    _ab, name, satz = ZEITALTER[nummer]
    return nummer, name, satz


def naechstes_zeitalter(anteil: float) -> tuple[str, int] | None:
    """Name des nächsten Zeitalters und wie viel Prozent bis dahin fehlen."""
    for ab, name, _satz in ZEITALTER:
        if anteil < ab:
            return name, max(1, round((ab - anteil) * 100))
    return None


@dataclass(frozen=True)
class Ressourcen:
    belege: int  # Dateien
    briefe: int  # Wünsche der Beraterin
    siegel: int  # fertige Provinzen
    fragen: int
    gold: int = 0  # gefundene Bonus-Schätze


def ressourcen(stuecke: list[dict], provinzstaende: list[Provinzstand]) -> Ressourcen:
    return Ressourcen(
        belege=sum(s.get("dateien", 0) for s in stuecke),
        briefe=sum(1 for s in stuecke if s["wunsch"]),
        siegel=sum(1 for p in provinzstaende if p.stand.siegel and not p.nur_bonus),
        fragen=sum(1 for s in stuecke if s["status"] == "fragen"),
        gold=sum(1 for s in stuecke if ist_bonus(s) and s["status"] == "gesammelt"),
    )


# ---------- Der Papierdrache ----------

RUFE = (
    "Er wirft dir eine Karte zu!",
    "Treffer!",
    "Doppelschlag!",
    "Dreifach-Serie!",
    "Unaufhaltsam!",
    "Legendäre Serie!",
)


@dataclass(frozen=True)
class Drache:
    """Der Bosskampf am Ende. Seine Lebenspunkte sind die offenen Sammelstücke.

    Je mehr du vorher gesammelt hast, desto weniger Karten hat er. Jede beantwortete
    Karte ist ein Treffer: eine Datei abgeben ist ein kritischer Treffer, "gibt's nicht"
    oder "lieber fragen" sind normale Treffer. Nichts davon ist falsch.
    """

    lp: int
    max_lp: int
    karten: list[dict] = field(default_factory=list)

    @property
    def besiegt(self) -> bool:
        return self.lp == 0

    @property
    def prozent(self) -> int:
        return round(100 * self.lp / self.max_lp) if self.max_lp else 0

    @property
    def stimmung(self) -> str:
        if self.besiegt:
            return "besiegt"
        if self.prozent <= 25:
            return "taumelt"
        if self.prozent <= 60:
            return "verunsichert"
        return "schläfrig"

    @property
    def karte(self) -> dict | None:
        return self.karten[0] if self.karten else None


def drache(stuecke: list[dict]) -> Drache:
    pflichten = pflicht(stuecke)
    karten = [s for s in pflichten if s["status"] == "offen"]
    # Wünsche der Beraterin zuerst, die sind ihr wichtig.
    karten.sort(key=lambda s: (-s["wunsch"], s["id"]))
    return Drache(lp=len(karten), max_lp=len(pflichten), karten=karten)


def treffer(combo: int, kritisch: bool, zaehlt: bool = True) -> tuple[int, str]:
    """Neue Combo-Zahl und der passende Ruf. Kritische Treffer (eine Datei) zählen doppelt.

    "Gibt's nicht" zählt nicht für die Serie (zaehlt=False): Die Serie hält, wächst aber nicht.
    Sonst wäre Wegstempeln der schnellste Weg zum Sieg, und genau das soll das Spiel nicht belohnen.
    """
    if not zaehlt:
        return combo, "Notiert. Die Serie hält."
    neu = combo + (2 if kritisch else 1)
    ruf = RUFE[min(neu, len(RUFE) - 1)]
    if kritisch:
        ruf = "Kritischer Treffer! " + ruf
    return neu, ruf


def drache_wach(stuecke: list[dict]) -> bool:
    """Der Drache wacht erst im Zeitalter der Ordnung auf: Erst wird gebaut, dann gekämpft."""
    return gesamtstand(stuecke).anteil >= ZEITALTER[1][0]


# ---------- Erfolge ----------


ORDEN_ZEICHEN = {
    "erster-fund": "1",
    "sammlerin": "25",
    "erste-ernte": "❦",
    "brieftaube": "✉\ufe0e",
    "gedaechtnis": "↺",
    "schatzsucherin": "◆",
    "mutig-gefragt": "?",
    "drachenbaendigerin": "♛\ufe0e",
}


@dataclass(frozen=True)
class Erfolg:
    key: str
    name: str
    beschreibung: str
    erreicht: bool

    @property
    def zeichen(self) -> str:
        """Ein eigenes Zeichen pro Orden, damit keine zwei gleich aussehen."""
        return ORDEN_ZEICHEN.get(self.key, self.name[:1])


def erfolge(
    stuecke: list[dict],
    provinzstaende: list[Provinzstand],
    vergleich: list[dict] | None = None,
    brief_gelesen: bool = False,
) -> list[Erfolg]:
    r = ressourcen(stuecke, provinzstaende)
    gesamt = gesamtstand(stuecke)
    vorjahr_ok = bool(vergleich) and all(v["jetzt"] in ERLEDIGT for v in vergleich)
    return [
        Erfolg("erster-fund", "Erster Fund", "Das erste Dokument ist gesichert.", r.belege >= 1),
        Erfolg("sammlerin", "Sammlerin", "25 Dokumente gesichert.", r.belege >= 25),
        Erfolg("erste-ernte", "Erste Ernte", "Eine Provinz ist komplett.", r.siegel >= 1),
        Erfolg("brieftaube", "Brieftaube", "Eine Mail der Beraterin übersetzt.", brief_gelesen),
        Erfolg(
            "gedaechtnis",
            "Gutes Gedächtnis",
            "Alles, was letztes Jahr da war, ist wieder da.",
            vorjahr_ok,
        ),
        Erfolg("schatzsucherin", "Schatzsucherin", "3 Bonus-Schätze gefunden.", r.gold >= 3),
        Erfolg(
            "mutig-gefragt",
            "Mutig gefragt",
            "Mindestens eine Frage gestellt statt geraten.",
            r.fragen >= 1,
        ),
        Erfolg(
            "drachenbaendigerin",
            "Drachenbändigerin",
            "Der Papierdrache hat keine Karten mehr.",
            gesamt.offen == 0 and bool(stuecke),
        ),
    ]


def neue_erfolge(vorher: list[Erfolg], nachher: list[Erfolg]) -> list[Erfolg]:
    alt = {e.key for e in vorher if e.erreicht}
    return [e for e in nachher if e.erreicht and e.key not in alt]


# ---------- Der nächste Auftrag ----------
# Gute Spiele lassen dich nie raten, was als Nächstes kommt: Es gibt immer genau einen
# klaren Auftrag (Zelda, Stardew Valley), er ist klein genug für die nächsten Minuten,
# und die Belohnung steht schon dabei. Am liebsten der Auftrag, der fast fertig ist:
# Je näher das Ziel, desto mehr Lust hat man (Zeigarnik und "goal gradient").


@dataclass(frozen=True)
class Auftrag:
    art: str  # korb | provinz | sieg | start
    titel: str
    text: str
    knopf: str
    ziel: str  # Adresse, zu der der Knopf führt
    belohnungen: tuple[str, ...] = ()
    gebiet_id: str = ""
    erledigt: int = 0
    gesamt: int = 0


def auftrag(
    jahr: int, staende: list[Provinzstand], drache_: Drache, zettel: int, stuecke: list[dict]
) -> Auftrag:
    """Genau ein klarer nächster Schritt, mit Belohnung."""
    basis = f"/steuer/{jahr}"
    if drache_.besiegt and stuecke:
        return Auftrag(
            "sieg",
            "Hol dir deine Beute",
            "Der Drache ist besiegt. Pack die Schatztruhe für deine Beraterin.",
            "Zur Schatztruhe",
            f"{basis}/sieg",
            ("Goldenes Zeitalter",),
        )
    if zettel:
        return Auftrag(
            "korb",
            "Sortiere den Eingangskorb",
            (
                "Ein Zettel wartet. Zieh ihn auf seine Provinz."
                if zettel == 1
                else f"{zettel} Zettel warten. Zieh jeden auf seine Provinz."
            ),
            "Ersten Zettel ansehen",
            "#sq-korb",
            ("+1 Beleg pro Zettel",),
            erledigt=0,
            gesamt=zettel,
        )
    offen = [p for p in staende if not p.stand.siegel]
    if not offen:
        return Auftrag(
            "sieg",
            "Stell dich dem Drachen",
            "Alle Provinzen tragen ihr Siegel.",
            "Zum Drachenhort",
            f"{basis}/drache",
        )
    # Angefangene Provinzen zuerst, dann die mit den wenigsten offenen Karten.
    ziel = min(offen, key=lambda p: (p.stand.erledigt == 0, p.stand.offen))
    gesamt = gesamtstand(stuecke)
    jetzt = zeitalter(gesamt.anteil)[0]
    danach_anteil = (gesamt.erledigt + ziel.stand.offen) / gesamt.gesamt if gesamt.gesamt else 1
    belohnungen = ["Gold-Siegel"]
    if zeitalter(danach_anteil)[0] > jetzt:
        belohnungen.append(f"Aufstieg ins {zeitalter(danach_anteil)[1]}")
    if not any(p.stand.siegel for p in staende):
        belohnungen.append("Orden „Erste Ernte“")
    rest = ziel.stand.offen
    karten = "eine Karte" if rest == 1 else f"{rest} Karten"
    return Auftrag(
        "provinz",
        f"Versiegle {ziel.gebiet.titel}",
        f"Noch {karten}. Leg ab, was du hast. Was es nicht gibt, stempelst du einfach ab.",
        "Los geht's",
        f"{basis}/gebiet/{ziel.gebiet.id}",
        tuple(belohnungen),
        gebiet_id=ziel.gebiet.id,
        erledigt=ziel.stand.erledigt,
        gesamt=ziel.stand.gesamt,
    )


# ---------- Tagwerk: ein kleines, festes Tagesziel (Slow Productivity statt Marathon) ----------

TAGWERK = 3


def tagwerk(stuecke: list[dict], heute: date, ziel: int = TAGWERK) -> tuple[int, int]:
    """(heute erledigte Karten, Tagesziel). Erledigt heißt: nicht mehr im Nebel."""
    tag = heute.isoformat()
    n = sum(
        1 for s in stuecke if s["status"] != "offen" and str(s.get("geaendert") or "")[:10] == tag
    )
    return n, ziel


def zweifel(stuecke: list[dict]) -> list[dict]:
    """Wichtige Unterlagen, die als "Gibt's nicht" gestempelt sind: die soll die Beraterin prüfen."""
    return [s for s in stuecke if s["status"] == "gibts_nicht" and ist_kern(s["gebiet"], s["key"])]
