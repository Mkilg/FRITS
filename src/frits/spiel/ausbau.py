"""Frittes Haus: built out stage by stage, like a little settlement growing through its ages.

Each stage costs Salzkörner and brings something lasting: another place on the spice
shelf, rarer spices at the market, more Würze to start with. Building takes until the
next Kassensturz: the scaffolding stands in the room, and the Kassensturz finishes it.

Once the Frittenbude stands, Fritte can open the next one in a new place (Umzug). The
house starts again as a room, but she takes her Ruf along: every finished Bude adds
Würze to every Kassensturz for good, the shelf and the market stay as big as they were.
Building costs more in each new place, so there is always a next goal.
"""

from __future__ import annotations

from dataclasses import dataclass

from frits import store
from frits.spiel import stand


@dataclass(frozen=True)
class Stufe:
    nummer: int
    name: str
    zeichen: str
    preis: int
    text: str
    plaetze: int  # extra spice shelf places compared to the start
    seltenheiten: tuple[str, ...]  # what the market may offer
    grundwuerze: float  # Würze every Kassensturz starts with


STUFEN: tuple[Stufe, ...] = (
    Stufe(1, "Zimmer", "🛏️", 0, "Ein Fenster, ein Teppich, ein Anfang.",
          0, ("gewöhnlich",), 1.0),
    Stufe(2, "Stube", "🛋️", 80, "Holzvertäfelung, Vorhänge und eine Lampe. "
          "+1 Regalplatz, seltene Gewürze auf dem Markt.", 1, ("gewöhnlich", "selten"), 1.0),
    Stufe(3, "Werkstatt", "🔨", 200, "Eine Werkbank für große Vorhaben. "
          "+1 Regalplatz, legendäre Gewürze auf dem Markt.",
          2, ("gewöhnlich", "selten", "legendär"), 1.0),
    Stufe(4, "Hof mit Garten", "🌻", 450, "Vor dem Fenster wächst ein Garten. "
          "+1 Regalplatz, jeder Kassensturz beginnt mit Würze 1,5.",
          3, ("gewöhnlich", "selten", "legendär"), 1.5),
    Stufe(5, "Frittenbude", "🎪", 900, "Mitten im Ort, mit Markise und Lichterkette. "
          "+1 Regalplatz, jeder Kassensturz beginnt mit Würze 2.",
          4, ("gewöhnlich", "selten", "legendär"), 2.0),
)  # fmt: skip


@dataclass(frozen=True)
class Ort:
    nummer: int
    name: str
    zeichen: str
    text: str


ORTE: tuple[Ort, ...] = (
    Ort(1, "Am Markt", "🏘️", "Der Marktplatz, wo alles anfing."),
    Ort(2, "Am Hafen", "⚓", "Möwen, Fischkutter und viel Kundschaft mit Hunger."),
    Ort(3, "Im Bergdorf", "⛰️", "Klare Luft, Wanderer, und abends wird es früh still."),
    Ort(4, "In der Altstadt", "🏰", "Kopfsteinpflaster und ein Brunnen vor der Tür."),
    Ort(5, "Am See", "🛶", "Ein Steg, ein Ruderboot und sehr lange Sommerabende."),
)  # fmt: skip
RUF_WUERZE = 0.5  # every finished Bude adds this much Würze to every Kassensturz
RUF_PREIS = 0.5  # building costs this much more in each new place
ORT_STUFE_WUERZE = 0.1  # in a new place, every stage built adds this much Würze
UMZUG_PREIS = 1200  # times the number of the place Fritte leaves


def ort(conn) -> Ort:
    n = int(stand.einstellung(conn, "ort", "1") or 1)
    return ORTE[max(1, min(n, len(ORTE))) - 1]


def ruf(conn) -> int:
    """How many Frittenbuden Fritte has finished and left behind."""
    return int(stand.einstellung(conn, "ort", "1") or 1) - 1


def preis(conn, ziel: Stufe) -> int:
    return round(ziel.preis * (1 + RUF_PREIS * ruf(conn)))


def preisstufe(conn) -> int:
    """How far Fritte has come in all: the stage here plus four for every finished Bude.
    Prices follow it, so they don't drop when she starts again in a new place."""
    return stufe(conn).nummer + 4 * ruf(conn)


def grundwuerze(conn) -> float:
    """Never goes down: in a new place Fritte keeps the Würze of her finished Bude, the
    Ruf adds to it, and every stage built there adds a little more."""
    if not ruf(conn):
        return stufe(conn).grundwuerze
    return round(
        STUFEN[-1].grundwuerze
        + RUF_WUERZE * ruf(conn)
        + ORT_STUFE_WUERZE * (stufe(conn).nummer - 1),
        2,
    )


def stufen_text(conn, ziel: Stufe) -> str:
    """What a stage brings: in a new place the shelf and the market are already big."""
    if not ruf(conn):
        return ziel.text
    bonus = f"{ORT_STUFE_WUERZE:g}".replace(".", ",")
    return f"{ziel.text.split('. ')[0]}. Hier bringt jede Stufe +{bonus} Würze für immer."


def extra_plaetze(conn) -> int:
    return STUFEN[-1].plaetze if ruf(conn) else stufe(conn).plaetze


def seltenheiten(conn) -> tuple[str, ...]:
    return STUFEN[-1].seltenheiten if ruf(conn) else stufe(conn).seltenheiten


def naechster_ort(conn) -> Ort | None:
    n = ort(conn).nummer
    return ORTE[n] if n < len(ORTE) else None


def umzug_preis(conn) -> int:
    return UMZUG_PREIS * ort(conn).nummer


def umziehen(conn, heute) -> Ort:
    """Open the next Frittenbude somewhere new. Fritte takes her things, her shelf and her
    Ruf along; the house starts again as a room."""
    stand.vorbereiten(conn)
    with store.transaction(conn):
        if stufe(conn).nummer < len(STUFEN) or im_bau(conn):
            raise stand.SpielFehler(
                "Erst wenn die Frittenbude fertig ist, kann Fritte weiterziehen."
            )
        ziel = naechster_ort(conn)
        if ziel is None:
            raise stand.SpielFehler(
                "Fritte hat schon an jedem Ort eine Bude. Mehr Orte gibt es nicht."
            )
        stand.bezahlen(conn, heute.isoformat(), umzug_preis(conn), f"Umzug: {ziel.name}")
        stand.setze(conn, "ort", str(ziel.nummer))
        stand.setze(conn, "ausbau", "1")
        stand.setze(conn, "ausbau_bau", "0")
    return ziel


def stufe(conn) -> Stufe:
    n = int(stand.einstellung(conn, "ausbau", "1") or 1)
    return STUFEN[max(1, min(n, len(STUFEN))) - 1]


def im_bau(conn) -> Stufe | None:
    n = int(stand.einstellung(conn, "ausbau_bau", "0") or 0)
    return STUFEN[n - 1] if 1 < n <= len(STUFEN) else None


def naechste(conn) -> Stufe | None:
    n = stufe(conn).nummer
    return STUFEN[n] if n < len(STUFEN) else None


def ausbauen(conn, heute) -> Stufe:
    """Pay for the next stage and put up the scaffolding."""
    stand.vorbereiten(conn)
    with store.transaction(conn):
        if im_bau(conn):
            raise stand.SpielFehler(
                "Es wird schon gebaut. Der nächste Kassensturz macht es fertig."
            )
        ziel = naechste(conn)
        if ziel is None:
            raise stand.SpielFehler("Frittes Haus ist fertig ausgebaut. Mehr geht nicht.")
        stand.bezahlen(conn, heute.isoformat(), preis(conn, ziel), f"Ausbau: {ziel.name}")
        stand.setze(conn, "ausbau_bau", str(ziel.nummer))
    return ziel


def fertigstellen(conn) -> Stufe | None:
    """Called inside the Kassensturz transaction: the building work is done."""
    ziel = im_bau(conn)
    if ziel is None:
        return None
    stand.setze(conn, "ausbau", str(ziel.nummer))
    stand.setze(conn, "ausbau_bau", "0")
    return ziel
