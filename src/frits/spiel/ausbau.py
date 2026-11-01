"""Frittes Haus: built out stage by stage, like a little settlement growing through its ages.

Each stage costs Salzkörner and brings something lasting: another place on the spice
shelf, rarer spices at the market, more Würze to start with. Building takes until the
next Kassensturz: the scaffolding stands in the room, and the Kassensturz finishes it.
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
    Stufe(5, "Frittenbude am Markt", "🎪", 900, "Mitten im Ort, mit Lichterkette. "
          "+1 Regalplatz, jeder Kassensturz beginnt mit Würze 2.",
          4, ("gewöhnlich", "selten", "legendär"), 2.0),
)  # fmt: skip


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
        stand.bezahlen(conn, heute.isoformat(), ziel.preis, f"Ausbau: {ziel.name}")
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
