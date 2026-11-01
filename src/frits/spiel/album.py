"""Frittes Album: stickers for milestones. Only ever gained, never lost.

Everything is worked out from what already happened (the saved Kassenstürze, the house,
what Fritte owns), so nothing new has to be tracked. The day a sticker was first seen
is kept, so the album can say "neu" and the Kassensturz can show it once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from frits.spiel import ausbau, stand


@dataclass(frozen=True)
class Erfolg:
    id: str
    name: str
    zeichen: str
    text: str
    pruefen: Callable[[dict], bool]


def _zaehle(art: str, fakt: str | None = None):
    def zahl(fakten: dict) -> int:
        return sum(1 for k in fakten["karten"] if k["art"] == art and (fakt is None or k.get(fakt)))

    return zahl


ERFOLGE: tuple[Erfolg, ...] = (
    Erfolg("erster", "Der erste Kassensturz", "🧾", "Den ersten Kassensturz gemacht.",
           lambda f: f["stuerze"] >= 1),
    Erfolg("bunt", "Bunte Mischung", "🎨", "Vier verschiedene Kartenarten in einem Kassensturz.",
           lambda f: f["meiste_arten"] >= 4),
    Erfolg("hundert", "Großer Wurf", "💯", "100 Salzkörner in einem einzigen Kassensturz.",
           lambda f: f["bester"] >= 100),
    Erfolg("fuenfhundert", "Goldene Fritte", "🏆", "500 Salzkörner in einem einzigen Kassensturz.",
           lambda f: f["bester"] >= 500),
    Erfolg("zeitnah", "Beleg-Profi", "🧾", "Zehn Belege innerhalb von zehn Tagen eingetragen.",
           lambda f: _zaehle("ausgabe", "zeitnah")(f) >= 10),
    Erfolg("frisch", "Frisch geschrieben", "✍️",
           "Zehn Rechnungen höchstens eine Woche nach der Arbeit geschrieben.",
           lambda f: _zaehle("rechnung", "frisch")(f) >= 10),
    Erfolg("puenktlich", "Gute Kundschaft", "🤝", "Fünf Zahlungen kamen vor dem Zahlungsziel.",
           lambda f: _zaehle("bezahlt", "puenktlich")(f) >= 5),
    Erfolg("ritual", "Feierabend-Ritual", "🌙", "Zehnmal bewusst Feierabend gemacht.",
           lambda f: _zaehle("feierabend")(f) >= 10),
    Erfolg("rhythmus", "Guter Rhythmus", "🎵", "In vier Wochen hintereinander Papierkram gemacht.",
           lambda f: f["rhythmus"] >= 4),
    Erfolg("regal", "Volles Regal", "🫙", "Jeden Platz im Gewürzregal gefüllt.",
           lambda f: f["regal"] >= f["plaetze"]),
    Erfolg("legende", "Legendär", "✨", "Ein legendäres Gewürz ins Regal gestellt.",
           lambda f: f["legendaer"]),
    Erfolg("gemuetlich", "Gut eingerichtet", "🛋️", "Acht Dinge im Zimmer.",
           lambda f: f["dinge"] >= 8),
    Erfolg("huete", "Hutsammlung", "🎩", "Alle Hüte im Schrank.",
           lambda f: f["huete"] >= 5),
    Erfolg("stube", "Die Stube", "🛋️", "Frittes Haus zur Stube ausgebaut.",
           lambda f: f["stufe"] >= 2),
    Erfolg("werkstatt", "Die Werkstatt", "🔨", "Frittes Haus zur Werkstatt ausgebaut.",
           lambda f: f["stufe"] >= 3),
    Erfolg("hof", "Hof mit Garten", "🌻", "Frittes Haus zum Hof mit Garten ausgebaut.",
           lambda f: f["stufe"] >= 4),
    Erfolg("bude", "Frittenbude am Markt", "🎪", "Frittes Haus ganz ausgebaut.",
           lambda f: f["stufe"] >= 5),
)  # fmt: skip


def _fakten(conn, heute: date) -> dict:
    from frits import haustier
    from frits.spiel import kassensturz

    karten, bester, meiste_arten, stuerze = [], 0, 0, 0
    for summe, ergebnis in conn.execute(
        "SELECT summe, ergebnis FROM spiel_kassenstuerze"
    ).fetchall():
        stuerze += 1
        bester = max(bester, int(summe))
        these = json.loads(ergebnis).get("karten", [])
        karten += these
        meiste_arten = max(meiste_arten, len({k["art"] for k in these}))
    regal = kassensturz.regal(conn)
    dinge = [d for d in stand.besitz(conn, "ding") if d in haustier.DINGE]
    return {
        "karten": karten,
        "bester": bester,
        "meiste_arten": meiste_arten,
        "stuerze": stuerze,
        "rhythmus": kassensturz.rhythmus_wochen(conn, heute, wochen=4),
        "regal": len(regal),
        "plaetze": kassensturz.plaetze(conn),
        "legendaer": any(g.seltenheit == "legendär" for g in regal),
        "dinge": sum(1 for d in dinge if haustier.DINGE[d].art == "zimmer"),
        "huete": sum(1 for d in dinge if haustier.DINGE[d].art == "hut"),
        "stufe": ausbau.stufe(conn).nummer,
    }


def neue(conn, heute: date) -> list[Erfolg]:
    """Stickers earned but not yet in the album. They are put in right away."""
    stand.vorbereiten(conn)
    fakten = _fakten(conn, heute)
    found = []
    for e in ERFOLGE:
        key = f"erfolg:{e.id}"
        if not stand.einstellung(conn, key) and e.pruefen(fakten):
            stand.setze(conn, key, heute.isoformat())
            found.append(e)
    return found


def seiten(conn, heute: date) -> list[dict]:
    neue(conn, heute)
    return [{"erfolg": e, "seit": stand.einstellung(conn, f"erfolg:{e.id}")} for e in ERFOLGE]
