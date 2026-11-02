"""One collection instead of three (Lena): Fritte's stickers, the treasures from her chests,
the Steuer-Quest's Orden and the trophies and festivals of the seasons stand together in
one showcase.

Read only. The Orden come from the Steuer-Quest (frits.steuer.quest.orden_fuer_sammlung);
while that is missing or fails, the showcase simply shows the other shelves.
Each Orden unlocks a Zierde for the island; the quest keeps that, this only shows it.
"""

from __future__ import annotations

from datetime import date

from frits import haustier
from frits.spiel import album, saison
from frits.spiel import stand as spielstand


def _orden(conn) -> list[dict] | None:
    try:
        from frits.steuer import quest

        holen = getattr(quest, "orden_fuer_sammlung", None)
        if holen is None:
            return None
        roh = list(holen(conn))
    except Exception:  # noqa: BLE001  the quest is optional; the showcase must not break
        return None
    try:
        from frits.steuer import spiel

        zierde_name = {w.key: w.name for w in spiel.MARKT}
    except Exception:  # noqa: BLE001
        zierde_name = {}
    kacheln: dict[str, dict] = {}
    for o in roh:
        k = kacheln.setdefault(
            o["key"],
            {
                "id": f"orden:{o['key']}",
                "name": o["name"],
                "zeichen": o.get("zeichen") or "🏅",
                "text": o.get("beschreibung", ""),
                "erreicht": False,
                "jahre": [],
                "zierde": zierde_name.get(o.get("zierde") or "", o.get("zierde") or ""),
            },
        )
        if o.get("erreicht"):
            k["erreicht"] = True
            if o.get("jahr"):
                k["jahre"].append(int(o["jahr"]))
    for k in kacheln.values():
        k["jahre"].sort()
    return list(kacheln.values())


SELTEN_TEXT = {
    "gewöhnlich": "einer gewöhnlichen",
    "selten": "einer seltenen",
    "legendär": "einer legendären",
}


def _schaetze(conn) -> dict:
    """The treasures that only Fritte's chests hold (haustier.SCHAETZE)."""
    hat = set(spielstand.besitz(conn, "ding"))
    return {
        "id": "schaetze",
        "titel": "Schätze aus Frittes Truhen",
        "text": "Es gibt sie nur in Truhen und nie doppelt.",
        "kacheln": [
            {"id": f"schatz:{d.id}", "name": d.name, "zeichen": d.zeichen,
             "text": d.text if d.id in hat else f"Steckt in {SELTEN_TEXT.get(d.truhe, 'einer')} Truhe.",
             "erreicht": d.id in hat, "seit": "", "jahre": [], "zierde": ""}
            for d in haustier.SCHAETZE
        ],
    }  # fmt: skip


def faecher(conn, heute: date, alltag: bool = True) -> list[dict]:
    """The shelves of the showcase, each {id, titel, text, kacheln}. A tile is
    {id, name, zeichen, text, erreicht, seit, jahre, zierde}. `alltag`: with Fritte."""
    out = []
    orden = _orden(conn)
    # The quest's own Orden for the dragon says the same as Fritte's sticker: show it once.
    doppelt = {"drache"} if orden else set()
    if alltag:
        out.append(
            {
                "id": "alltag",
                "titel": "Alltag mit Fritte",
                "text": "Für gute Gewohnheiten und große Momente im Papierkram.",
                "kacheln": [
                    {
                        "id": f"sticker:{s['erfolg'].id}",
                        "name": s["erfolg"].name,
                        "zeichen": s["erfolg"].zeichen,
                        "text": s["erfolg"].text,
                        "erreicht": bool(s["seit"]),
                        "seit": s["seit"],
                        "jahre": [],
                        "zierde": "",
                    }
                    for s in album.seiten(conn, heute)
                    if s["erfolg"].id not in doppelt
                ],
            }
        )
    if alltag:
        out.append(_schaetze(conn))
    if orden:
        out.append(
            {
                "id": "steuer",
                "titel": "Steuer-Quest",
                "text": "Orden aus den Steuerjahren. Jeder Orden stellt eine Zierde auf die Insel.",
                "kacheln": orden,
            }
        )
    if alltag:
        pokale = sorted(
            (tuple(p.rsplit(" ", 1)) for p in saison.pokale(conn)),
            key=lambda p: (p[1], saison.REIHE.get(p[0], 9)),
        )
        kacheln = [
            {"id": f"pokal:{n} {j}", "name": f"{n} {j}", "zeichen": "🏆",
             "text": "Saisonziel geschafft.", "erreicht": True, "seit": "", "jahre": [],
             "zierde": ""}
            for n, j in pokale
        ]  # fmt: skip
        kacheln += [
            {"id": f"fest:{name}", "name": fest, "zeichen": "🎉",
             "text": f"{name}, {ort[0].lower() + ort[1:]}.", "erreicht": True, "seit": "",
             "jahre": [], "zierde": ""}
            for name, fest, ort in saison.feste(conn)
        ]  # fmt: skip
        if kacheln:
            out.append(
                {
                    "id": "saisons",
                    "titel": "Saisons und Feste",
                    "text": "Pokale für geschaffte Saisonziele und die Feste in Frittes Buden.",
                    "kacheln": kacheln,
                }
            )
    return out


def stand(faecher_: list[dict]) -> tuple[int, int]:
    """(collected, places) over stickers and Orden; trophies and festivals are extras."""
    zaehlen = [k for f in faecher_ if f["id"] != "saisons" for k in f["kacheln"]]
    return sum(1 for k in zaehlen if k["erreicht"]), len(zaehlen)
