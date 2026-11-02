"""One collection instead of three (Lena): the Steuer-Quest's Orden, the treasures from
Fritte's chests, Fritte's stickers and the trophies and festivals of the seasons stand
together in one showcase.

A showcase, not a form (Lena, round 21): what was earned last stands on top, the Orden
come first, and each shelf shows only a few of its empty places at once.

Read only. The Orden come from the Steuer-Quest (frits.steuer.quest.orden_fuer_sammlung);
while that is missing or fails, the showcase simply shows the other shelves.
Each Orden unlocks a Zierde for the island; the quest keeps that, this only shows it.
"""

from __future__ import annotations

from datetime import date

from frits import haustier
from frits.spiel import album, saison
from frits.spiel import stand as spielstand

ZULETZT = 4  # tiles in "Zuletzt verdient"
OFFEN_ZEIGEN = 3  # empty places shown per shelf; the rest wait behind "Noch N Plätze"


def _kachel(id_: str, name: str, zeichen: str, text: str, erreicht: bool, **mehr) -> dict:
    """`zeit`: when it was earned, "YYYY-MM-DD" or with the time ("...T14:05:09");
    `seit` is its day, for the page."""
    k = {"id": id_, "name": name, "zeichen": zeichen, "text": text, "erreicht": erreicht,
         "zeit": "", "jahre": [], "zierde": "", **mehr}  # fmt: skip
    k["seit"] = (k["zeit"] or "")[:10]
    return k


def _orden_seit(conn) -> dict[str, str]:
    """When each Orden (by name) was last noted in the quest's chronicle."""
    try:
        return {
            r[0]: r[1] or ""
            for r in conn.execute(
                "SELECT text, MAX(zeit) FROM steuer_chronik WHERE art = 'erfolg' GROUP BY text"
            ).fetchall()
        }
    except Exception:  # noqa: BLE001  no quest tables yet
        return {}


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
    seit = _orden_seit(conn)
    kacheln: dict[str, dict] = {}
    for o in roh:
        k = kacheln.setdefault(
            o["key"],
            _kachel(
                f"orden:{o['key']}", o["name"], o.get("zeichen") or "🏅",
                o.get("beschreibung", ""), False,
                zierde=zierde_name.get(o.get("zierde") or "", o.get("zierde") or ""),
                zierde_key=o.get("zierde") or "",
            ),
        )  # fmt: skip
        if o.get("erreicht"):
            k["erreicht"] = True
            k["zeit"] = seit.get(o["name"], "")
            k["seit"] = k["zeit"][:10]
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
    spielstand.vorbereiten(conn)
    seit = {
        r[0]: r[1] or ""
        for r in conn.execute("SELECT id, gekauft FROM spiel_besitz WHERE art = 'ding'")
    }
    return {
        "id": "schaetze",
        "titel": "Schätze aus Frittes Truhen",
        "text": "Es gibt sie nur in Truhen und nie doppelt.",
        "kacheln": [
            _kachel(
                f"schatz:{d.id}", d.name, d.zeichen,
                d.text if d.id in seit else f"Steckt in {SELTEN_TEXT.get(d.truhe, 'einer')} Truhe.",
                d.id in seit, zeit=seit.get(d.id, ""), bild=f"ding-{d.id}",
            )
            for d in haustier.SCHAETZE
        ],
    }  # fmt: skip


def _alltag(conn, heute: date, doppelt: set[str]) -> dict:
    return {
        "id": "alltag",
        "titel": "Alltag mit Fritte",
        "text": "Für gute Gewohnheiten und große Momente im Papierkram.",
        "kacheln": [
            _kachel(
                f"sticker:{s['erfolg'].id}", s["erfolg"].name, s["erfolg"].zeichen,
                s["erfolg"].text, bool(s["seit"]), zeit=s["seit"] or "",
            )
            for s in album.seiten(conn, heute)
            if s["erfolg"].id not in doppelt
        ],
    }  # fmt: skip


def _saisons(conn, heute: date) -> dict:
    """Trophies and festivals so far, and this season's trophy and festival still to win,
    so the shelf is there from the start (Lena could not find it)."""
    pokale = sorted(
        (tuple(p.rsplit(" ", 1)) for p in saison.pokale(conn)),
        key=lambda p: (p[1], saison.REIHE.get(p[0], 9)),
    )
    kacheln = [
        _kachel(f"pokal:{n} {j}", f"{n} {j}", "🏆", "Saisonziel geschafft.", True)
        for n, j in pokale
    ]
    kacheln += [
        _kachel(f"fest:{name}", fest, "🎉", f"{name}, {ort[0].lower() + ort[1:]}.", True)
        for name, fest, ort in saison.feste(conn)
    ]
    s = saison.jetzt(conn, heute)
    if s.name not in saison.pokale(conn):
        kacheln.append(_kachel(
            f"pokal:{s.name}", f"Pokal {s.name}", "🏆",
            f"{s.ziel} von {s.wochen} Wochen mit Papierkram, bis zum {s.ende:%d.%m.}", False,
        ))  # fmt: skip
    if not s.fest and saison.fest_moeglich(conn):
        kacheln.append(_kachel(
            f"fest:{s.id}", saison.FESTE[int(s.id[-1])], "🎉",
            "Einmal in dieser Saison kann Fritte ein Fest geben.", False,
        ))  # fmt: skip
    return {
        "id": "saisons",
        "titel": "Saisons und Feste",
        "text": "Pokale für geschaffte Saisonziele und die Feste in Frittes Buden.",
        "kacheln": kacheln,
    }


def faecher(conn, heute: date, alltag: bool = True) -> list[dict]:
    """The shelves of the showcase, Orden first, each {id, titel, text, kacheln}.
    A tile is {id, name, zeichen, text, erreicht, seit, jahre, zierde} (Orden also
    zierde_key, treasures bild). `alltag`: with Fritte."""
    out = []
    orden = _orden(conn)
    if orden:
        out.append(
            {
                "id": "steuer",
                "titel": "Orden der Steuer-Quest",
                "text": "Aus den Steuerjahren. Jeder Orden stellt eine Zierde auf die Insel.",
                "kacheln": orden,
            }
        )
    if alltag:
        out.append(_schaetze(conn))
        # The quest's own Orden for the dragon says the same as Fritte's sticker: show it once.
        out.append(_alltag(conn, heute, {"drache"} if orden else set()))
        out.append(_saisons(conn, heute))
    for fach in out:
        da = [k for k in fach["kacheln"] if k["erreicht"]]
        offen = [k for k in fach["kacheln"] if not k["erreicht"]]
        fach["da"], fach["als_naechstes"], fach["verborgen"] = (
            da, offen[:OFFEN_ZEIGEN], offen[OFFEN_ZEIGEN:],
        )  # fmt: skip
    return out


def zuletzt(faecher_: list[dict], n: int = ZULETZT) -> list[dict]:
    """The newest earned tiles over all shelves, for the top of the showcase."""
    mit_tag = [k for f in faecher_ for k in f["kacheln"] if k["erreicht"] and k["zeit"]]
    return sorted(mit_tag, key=lambda k: k["zeit"], reverse=True)[:n]


def stand(faecher_: list[dict]) -> tuple[int, int]:
    """(collected, places) over stickers, treasures and Orden; trophies and festivals are extras."""
    zaehlen = [k for f in faecher_ if f["id"] != "saisons" for k in f["kacheln"]]
    return sum(1 for k in zaehlen if k["erreicht"]), len(zaehlen)
