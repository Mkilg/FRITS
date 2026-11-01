"""Die Spielregeln der Steuer-Quest. Reine Rechnerei, ohne Datenbank und ohne Dateien.

Ideen aus Spielen, die das gut machen:
- Dorfromantik: Jede Provinz wächst sichtbar mit jedem Dokument (Stufe 0 bis 3).
- Balatro: Mehrere Rückmeldungen auf einmal, und Serien werden immer lauter (Combo).
- Hades: Ein Bosskampf, der fair ist, und bei dem nichts verloren geht.

Wichtig: Die Regeln belohnen, sie bestrafen nie. Es gibt keine Fristen-Strafen,
keine verlorenen Punkte und immer einen Notausgang.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from frits.steuer.katalog import Gebiet, ist_kern, katalog

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
    def silber(self) -> bool:
        """Komplett, aber eine wichtige Unterlage ist als "Gibt's nicht" gestempelt: Silber statt Gold."""
        return self.stand.siegel and bool(zweifel(self.stuecke))

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


SILBER = (
    "Silbernes Zeitalter",
    "Alles beisammen. Ein paar wichtige Unterlagen prüft noch deine Beraterin.",
)


def zeitalter(anteil: float, silber: bool = False) -> tuple[int, str, str]:
    """(Nummer, Name, Satz) des aktuellen Zeitalters.

    Mit silber=True (wichtige Unterlagen als "Gibt's nicht" gestempelt) heißt das letzte
    Zeitalter ehrlich "Silbernes Zeitalter" statt "Goldenes Zeitalter".
    """
    nummer = 0
    for i, (ab, _name, _satz) in enumerate(ZEITALTER):
        if anteil >= ab:
            nummer = i
    _ab, name, satz = ZEITALTER[nummer]
    if silber and nummer == len(ZEITALTER) - 1:
        name, satz = SILBER
    return nummer, name, satz


def aufstieg(name: str) -> str:
    """ "Aufstieg ins Goldene Zeitalter" (nicht "ins Goldenes"): das Adjektiv nach "ins" beugen."""
    erstes, _, rest = name.partition(" ")
    if erstes.endswith("es") and rest == "Zeitalter":
        name = f"{erstes[:-1]} {rest}"
    return f"Aufstieg ins {name}"


def naechstes_zeitalter(anteil: float) -> tuple[str, int] | None:
    """Name des nächsten Zeitalters und wie viel Prozent bis dahin fehlen."""
    for ab, name, _satz in ZEITALTER:
        if anteil < ab:
            return name, max(1, round((ab - anteil) * 100))
    return None


# Was das nächste Zeitalter bringt: steht im Zeitalter-Zettel oben in der Leiste
ZEITALTER_BRINGT = {
    "Zeitalter der Ordnung": "Dann wacht der Papierdrache auf, und der Endkampf beginnt.",
    "Goldenes Zeitalter": "Dann ist alles beisammen, und die Schatztruhe für deine Beraterin ist fertig.",
}


def weg_zum_zeitalter(stand: Stand) -> tuple[str, int, str] | None:
    """(Name, wie viele Karten noch fehlen, was es bringt) für das nächste Zeitalter."""
    for ab, name, _satz in ZEITALTER:
        if stand.anteil < ab:
            karten = max(1, math.ceil(ab * stand.gesamt - 1e-9) - stand.erledigt)
            return name, karten, ZEITALTER_BRINGT.get(name, "")
    return None


@dataclass(frozen=True)
class Ressourcen:
    belege: int  # Dateien
    briefe: int  # Wünsche der Beraterin
    siegel: int  # fertige Provinzen
    fragen: int
    schaetze: int = 0  # gefundene Bonus-Schätze
    gold: int = 0  # Gold im Beutel (aus dem Goldbuch, siehe quest.gold)


def ressourcen(stuecke: list[dict], provinzstaende: list[Provinzstand]) -> Ressourcen:
    return Ressourcen(
        belege=sum(s.get("dateien", 0) for s in stuecke),
        briefe=sum(1 for s in stuecke if s["wunsch"]),
        siegel=sum(1 for p in provinzstaende if p.stand.siegel and not p.nur_bonus),
        fragen=sum(1 for s in stuecke if s["status"] == "fragen"),
        schaetze=sum(1 for s in stuecke if ist_bonus(s) and s["status"] == "gesammelt"),
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
    # Alles mit "Fragen" oder "Gibt's nicht" beantwortet, aber noch kein einziges Dokument:
    # Dann bleibt ihm ein letzter Lebenspunkt. Ohne Beleg gibt es keinen Sieg.
    will_beleg: bool = False

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
    will_beleg = (
        bool(pflichten) and not karten and not any(s["status"] == "gesammelt" for s in stuecke)
    )
    lp = 1 if will_beleg else len(karten)
    return Drache(lp=lp, max_lp=max(len(pflichten), lp), karten=karten, will_beleg=will_beleg)


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
        Erfolg("schatzsucherin", "Schatzsucherin", "3 Bonus-Schätze gefunden.", r.schaetze >= 3),
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
    jahr: int,
    staende: list[Provinzstand],
    drache_: Drache,
    zettel: int,
    stuecke: list[dict],
    drache_wach: bool = False,
) -> Auftrag:
    """Genau ein klarer nächster Schritt, mit Belohnung.

    Reihenfolge: Beute abholen, dann der wache Drache (der Bosskampf ist ab dem Erwachen
    das Ziel, sonst verpasst man ihn; der Korb ist dort die Hand), dann der Eingangskorb,
    sonst die nächste Provinz.
    """
    basis = f"/steuer/{jahr}"
    if drache_.besiegt and stuecke:
        return Auftrag(
            "sieg",
            "Hol dir deine Beute",
            "Der Drache ist besiegt. Pack die Schatztruhe für deine Beraterin.",
            "Zur Schatztruhe",
            f"{basis}/sieg",
            ("Mail an die Beraterin",),
        )
    if drache_.will_beleg and (drache_wach or zettel == 0):
        return Auftrag(
            "drache",
            "Zeig dem Drachen einen echten Beleg",
            "Alle Karten sind beantwortet, aber noch kein einziges Dokument ist dabei. "
            "Leg mindestens eine Datei ab, zum Beispiel den letzten Steuerbescheid.",
            "Zur Insel",
            basis,
            ("Sieg", "Schatztruhe"),
            erledigt=drache_.max_lp - 1,
            gesamt=drache_.max_lp,
        )
    if drache_wach and drache_.lp and stuecke:
        lp = drache_.lp
        return Auftrag(
            "drache",
            "Besiege den Papierdrachen",
            (
                "Er hat nur noch eine Karte. Ein letzter Treffer!"
                if lp == 1
                else f"Er hat noch {lp} Karten. Jede Antwort ist ein Treffer, auch eine Frage."
            )
            + (
                f" Deine {zettel} Zettel aus dem Korb hast du als Hand dabei."
                if zettel > 1
                else (" Den Zettel aus dem Korb hast du als Hand dabei." if zettel else "")
            ),
            "Zum Drachenhort",
            f"{basis}/drache",
            ("Sieg", "Schatztruhe"),
            erledigt=drache_.max_lp - lp,
            gesamt=drache_.max_lp,
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
        belohnungen.append(aufstieg(zeitalter(danach_anteil)[1]))
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
    """(heute erledigte Karten, Tagesziel).

    Es zählen nur echte Fortschritte: eine Datei oder eine Frage an die Beraterin.
    "Gibt's nicht" zählt nicht, sonst wäre Wegstempeln der schnellste Weg zum Tagwerk.
    """
    tag = heute.isoformat()
    n = sum(
        1
        for s in stuecke
        if s["status"] in ("gesammelt", "fragen") and str(s.get("geaendert") or "")[:10] == tag
    )
    return n, ziel


def zweifel(stuecke: list[dict]) -> list[dict]:
    """Wichtige Unterlagen, die als "Gibt's nicht" gestempelt sind: die soll die Beraterin prüfen."""
    return [s for s in stuecke if s["status"] == "gibts_nicht" and ist_kern(s["gebiet"], s["key"])]


# ---------- Gold: die Wirtschaft der Insel ----------
# Balatro rechnet "Chips mal Multiplikator": Jede Karte bringt etwas Gold, die Serie im
# Drachenkampf multipliziert es. Age of Empires: Mit dem Gold baust du dein Reich aus.
# Ehrlich bleibt es trotzdem: Finden bringt am meisten, Fragen mehr als Wegstempeln,
# und jede Karte zahlt nur einmal aus (Stempel zurücknehmen und neu setzen bringt nichts).

GOLD_FUND = 3  # eine Unterlage gesichert
GOLD_SCHATZ = 10  # ein Bonus-Schatz: kann Steuern sparen
GOLD_FRAGE = 1  # lieber gefragt (ohne Serien-Multiplikator)
GOLD_GIBTS_NICHT = 1  # gibt es nicht (wichtige Unterlagen: 0)
GOLD_SIEGEL = 10  # höchstens, für eine Provinz voller Funde (siehe siegel_gold)
GOLD_TAGWERK = 5  # das Tagwerk geschafft
MAX_MULT = 8
HELFER_PLAETZE = 2
TAUSCH_PREIS = 5  # einen Helfer an den Tisch holen, der gerade Pause macht
WUNSCH_DECKEL = 5  # so viele erfüllte Wünsche der Beraterin zahlen Gold, pro Jahr
GOLD_JAHRE = 2  # Gold gibt es für dieses Jahr und die zwei davor (Abgabe mit Beraterin)


def gold_jahr(jahr: int, heute: date) -> bool:
    """Bringt eine Quest für dieses Steuerjahr Gold? Nur die letzten Jahre: Mit Beraterin
    gibt man die Erklärung oft erst im übernächsten Frühjahr ab (2025 also bis Anfang 2027).
    Ältere Jahre darf man trotzdem nachholen, sie füllen nur nicht mehr den Beutel. Sonst
    ließe sich mit erfundenen Jahren beliebig Gold für die Insel sammeln."""
    return heute.year - GOLD_JAHRE <= jahr <= heute.year


@dataclass(frozen=True)
class Ware:
    key: str
    name: str
    preis: int
    art: str  # "zierde" (steht auf der Insel, in allen Jahren) oder "helfer" (pro Jahr)
    text: str
    haken: str = ""  # Helfer haben wie gute Joker immer auch einen Nachteil
    platz: tuple[float, float] = (0.0, 0.0)  # Zierde: Lieblingsplatz auf der gemalten Insel


MARKT: tuple[Ware, ...] = (
    Ware(
        "spuernase",
        "Spürnase",
        12,
        "helfer",
        "Stempelt alle Vorschläge im Korb auf einmal ab, statt Zettel für Zettel.",
        haken="Sie frisst mit: Funde bringen 2 Gold statt 3.",
    ),
    Ware(
        "brieftaube",
        "Brieftaube",
        8,
        "helfer",
        "Fragen an die Beraterin bringen 2 Gold statt 1.",
        haken="„Gibt's nicht“ bringt kein Gold mehr.",
    ),
    Ware(
        "goldwaage",
        "Goldwaage",
        15,
        "helfer",
        "Bonus-Schätze wiegen doppelt: 20 Gold statt 10.",
        haken="Fragen bringen kein Gold mehr.",
    ),
    Ware(
        "sanduhr",
        "Sanduhr",
        8,
        "helfer",
        "Das Tagwerk bringt 15 Gold statt 5. Wenig am Tag, aber jeden Tag.",
        haken="Im Drachenkampf zählt die Serie höchstens ×3.",
    ),
    Ware(
        "trommel",
        "Kriegstrommel",
        18,
        "helfer",
        "Im Drachenkampf zählt die Serie eins mehr: aus ×2 wird ×3.",
        haken="„Gibt's nicht“ im Kampf setzt die Serie auf null.",
    ),
    Ware(
        "laternen",
        "Laternen",
        10,
        "zierde",
        "Ein warmer Lichterweg über die Wiese.",
        platz=(44.0, 61.0),
    ),
    Ware(
        "kirschbaum",
        "Kirschbaum",
        15,
        "zierde",
        "Blüht jedes Steuerjahr wieder.",
        platz=(52.0, 62.0),
    ),
    Ware(
        "wappenfahne",
        "Wappenfahne",
        20,
        "zierde",
        "Dein Wappen weht über der Insel.",
        platz=(60.0, 61.0),
    ),
    Ware(
        "bienen",
        "Bienenstöcke",
        25,
        "zierde",
        "Fleißig, aber in ihrem eigenen Tempo.",
        platz=(68.0, 62.0),
    ),
    Ware("windmuehle", "Windmühle", 35, "zierde", "Mahlt langsam und stetig.", platz=(78.0, 61.0)),
    Ware(
        "leuchtturm",
        "Leuchtturm",
        50,
        "zierde",
        "Zeigt dir auch nächstes Jahr den Weg zurück.",
        platz=(88.0, 58.0),
    ),
)

# Die Wiese der gemalten Insel (Prozent: links, oben, rechts, unten) für die Füße der Zierde.
# Ist der Lieblingsplatz belegt, sucht steuer.js den nächsten freien Fleck auf dieser Wiese.
# Gekauftes wird nie versteckt: Ist gar nichts frei, steht es dort, wo es am wenigsten stört.
WIESE: tuple[float, float, float, float] = (37.0, 28.0, 90.0, 62.0)


def ware(key: str) -> Ware | None:
    return next((w for w in MARKT if w.key == key), None)


def multiplikator(combo: int, helfer: frozenset[str] | set[str] = frozenset()) -> int:
    """Die Serie im Drachenkampf als Multiplikator: ×1 bis ×8.

    Kriegstrommel: eins mehr. Sanduhr: höchstens ×3."""
    deckel = 3 if "sanduhr" in helfer else MAX_MULT
    mult = max(1, min(combo, deckel))
    if combo and "trommel" in helfer:
        mult += 1
    return mult


def ist_wunsch(stueck: dict) -> bool:
    return str(stueck.get("key", "")).startswith("wunsch-")


def gold_fuer(stueck: dict, status: str, helfer: frozenset[str] | set[str] = frozenset()) -> int:
    """Grundwert einer Karte. Nur Funde werden im Kampf mit der Serie multipliziert.

    Wünsche der Beraterin zahlen erst, wenn eine Datei dranhängt: Sonst ließe sich mit einer
    erfundenen "Beraterin-Mail" beliebig viel Gold erfragen."""
    if status == "gesammelt":
        if ist_bonus(stueck):
            return GOLD_SCHATZ * (2 if "goldwaage" in helfer else 1)
        return GOLD_FUND - (1 if "spuernase" in helfer else 0)
    if ist_wunsch(stueck):
        return 0
    if status == "fragen":
        wert = GOLD_FRAGE + (1 if "brieftaube" in helfer else 0)
        return 0 if "goldwaage" in helfer else wert
    if status == "gibts_nicht":
        if ist_kern(stueck["gebiet"], stueck["key"]) or "brieftaube" in helfer:
            return 0
        return GOLD_GIBTS_NICHT
    return 0


def siegel_gold(funde: int) -> int:
    """Ein Siegel zahlt nach Funden: 2 Gold und 2 je gefundener Unterlage, höchstens 10.
    Eine Provinz voller "Gibt's nicht" bekommt ihr Siegel, aber kaum Gold."""
    return min(GOLD_SIEGEL, 2 + 2 * funde)


def tagwerk_gold(helfer: frozenset[str] | set[str] = frozenset()) -> int:
    return GOLD_TAGWERK * (3 if "sanduhr" in helfer else 1)


def karten_grund(stueck: dict, art: str) -> str:
    """Der Grund im Goldbuch hängt an der Karte selbst (Gebiet und Schlüssel), nicht an der
    Datenbank-Nummer: Wer eine Karte über den Fragebogen löscht und neu anlegt, bekommt sie
    nicht zweimal bezahlt. Wünsche heißen nach ihrem Text."""
    ding = f"wunsch:{stueck['name']}" if ist_wunsch(stueck) else stueck["key"]
    return f"{art}:{stueck['gebiet']}:{ding}"


@dataclass(frozen=True)
class Verdienst:
    grund: str  # einmalig pro Jahr, z. B. "fund:werkstatt-1:belege" (siehe quest.verdiene)
    basis: int
    mult: int
    text: str

    @property
    def betrag(self) -> int:
        return self.basis * self.mult


def verdienste(
    vorher: list[dict],
    nachher: list[dict],
    mult: int = 1,
    helfer: frozenset[str] | set[str] = frozenset(),
) -> list[Verdienst]:
    """Was diese Änderung an Gold bringen kann. Ob es schon einmal ausgezahlt wurde,
    entscheidet das Goldbuch (gleicher Grund zahlt nie zweimal)."""
    alt = {s["id"]: s["status"] for s in vorher}
    neu = []
    for s in nachher:
        status, war = s["status"], alt.get(s["id"], "offen")
        if status == war or status == "offen":
            continue
        basis = gold_fuer(s, status, helfer)
        if not basis:
            continue
        # Finden zahlt extra aus, auch wenn die Karte vorher als Frage erledigt war
        art = "fund" if status == "gesammelt" else "antwort"
        # Fragen und Wünsche füttern keine Gold-Serie (Wünsche kann man selbst erfinden)
        faktor = mult if status == "gesammelt" and not ist_wunsch(s) else 1
        neu.append(Verdienst(karten_grund(s, art), basis, faktor, s["name"]))
    return neu


def rueckbuchungen(vorher: list[dict], nachher: list[dict]) -> list[tuple[str, str]]:
    """Karten, die wieder offen sind: ihr Gold geht zurück (Grund, Text). So bringt ein
    Fehlwurf bei ×8 nichts, und die Karte darf danach ehrlich neu verdienen."""
    alt = {s["id"]: s for s in vorher}
    zurueck = []
    for s in nachher:
        war = alt.get(s["id"])
        if s["status"] == "offen" and war and war["status"] != "offen":
            zurueck += [(karten_grund(war, art), war["name"]) for art in ("fund", "antwort")]
    return zurueck


# ---------- Spürnase: welcher Zettel passt zu welcher Karte? ----------

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_LEERWOERTER = {
    "scan",
    "scans",
    "foto",
    "fotos",
    "bild",
    "datei",
    "dokument",
    "kopie",
    "img",
    "image",
    "seite",
    "neu",
    "final",
    "version",
    "screenshot",
    "pdf",
    "jpg",
    "jpeg",
    "png",
    "heic",
    "mail",
    "anhang",
    "und",
    "der",
    "die",
    "das",
    "fuer",
    "von",
    "vom",
    "zum",
    "zur",
    "des",
    "dem",
    "den",
    "ein",
    "bei",
    "aus",
    "auf",
    "mit",
    "per",
    "bis",
    "doc",
    "dsc",
    "txt",
}


def _woerter(text: str) -> set[str]:
    """Wörter ab 3 Buchstaben ("GbR", "KSK") und Zahlen wie "1099", aber keine Jahreszahlen."""
    text = text.lower().translate(_UMLAUTE)
    return {
        w
        for w in re.findall(r"[a-z]{3,}|\d{4}", text)
        if w not in _LEERWOERTER and not re.fullmatch(r"(19|20)\d\d", w)
    }


def _naehe(a: str, b: str) -> int:
    if a == b:
        return len(a) + 2
    kurz, lang = sorted((a, b), key=len)
    if len(kurz) >= 6 and kurz in lang:
        return len(kurz)
    gemeinsam = 0
    for x, y in zip(a, b):
        if x != y:
            break
        gemeinsam += 1
    return gemeinsam if gemeinsam >= 5 else 0


@dataclass(frozen=True)
class Ahnung:
    """Wohin ein Zettel vielleicht gehört: eine Karte, oder wenigstens eine Provinz."""

    stueck: dict | None = None
    gebiet: str | None = None


def ahnung(dateiname: str, stuecke: list[dict]) -> Ahnung:
    """Rät nur nach dem Dateinamen, wohin ein Zettel gehört. Keine KI, nichts verlässt den
    Computer: Es vergleicht Wörter mit den Kartennamen (und ihren Zweitnamen wie "UStVA")
    und den Stichworten der Provinzen ("GbR", "1099"). Darum heißt es auch "vielleicht".
    Offene Karten haben Vorrang."""
    woerter = _woerter(dateiname.rsplit(".", 1)[0])
    if not woerter:
        return Ahnung()
    provinzen = katalog()

    def bestes(wort: str, ziel: set[str]) -> int:
        return max((_naehe(wort, b) for b in ziel), default=0)

    # Je Gebiet und Wort: wie gut das Wort zu den Stichworten der Provinz passt
    provinz_wort: dict[str, dict[str, int]] = {}
    for gid in dict.fromkeys(s["gebiet"] for s in stuecke):
        p = provinzen.get(gid.split("-")[0])
        stich = {w for x in (p.woerter if p else ()) for w in _woerter(x)}
        provinz_wort[gid] = {a: bestes(a, stich) for a in woerter}
    beste, punkte = None, 0
    # Bei Gleichstand gewinnt die Provinz, die im Katalog zuerst kommt: "UStVA" ohne "GbR"
    # gehört zum eigenen Betrieb, nicht zur Gesellschaft
    reihe = {key: i for i, key in enumerate(provinzen)}
    for s in sorted(stuecke, key=lambda s: (reihe.get(s["gebiet"].split("-")[0], 99), s["id"])):
        p = provinzen.get(s["gebiet"].split("-")[0])
        karte = next((k for k in (p.stuecke if p else ()) if k.key == s["key"]), None)
        extra = " ".join(karte.woerter) if karte else ""
        ziel = _woerter(f"{s['name']} {s['key'].replace('-', ' ')} {extra}")
        treffer = {a: bestes(a, ziel) for a in woerter}
        wert = max(treffer.values())
        if not wert:
            continue
        # Die Provinz zählt nur mit Wörtern, die nicht schon die Karte erklärt haben:
        # "GbR_Rechnung" gehört zur Gilde, auch wenn "Rechnung" ein Werkstatt-Wort ist
        rest = [provinz_wort[s["gebiet"]][a] for a, t in treffer.items() if not t]
        wert = wert * 2 + (1 if s["status"] == "offen" else 0) + max(rest, default=0)
        if wert > punkte:
            beste, punkte = s, wert
    if beste is not None:
        return Ahnung(beste, beste["gebiet"])
    wert = {gid: max(w.values()) for gid, w in provinz_wort.items()}
    gid = max(wert, key=wert.__getitem__, default=None)
    if gid is not None and wert[gid]:
        return Ahnung(None, gid)
    return Ahnung()


def vermute(dateiname: str, stuecke: list[dict]) -> dict | None:
    """Die Karte, zu der ein Zettel am ehesten passt (siehe ahnung)."""
    return ahnung(dateiname, stuecke).stueck
