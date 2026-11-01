"""Frittes Truhen: a little luck, earned only with real paperwork.

Every scored card of real paperwork (invoice, payment, expense, quote, reminder, a tax
document) puts something into Fritte's chest; five cards fill it. Now and then a card is
a golden Glückskarte and brings a chest of its own. Ticking off the list, learning, the
Feierabend or a correction never count: luck must not reward clicking around.

Fair on purpose, and said openly on the page:
- the odds are fixed and shown (60 % gewöhnlich, 30 % selten, 10 % legendär),
- at the latest every 4th chest is rare or better, every 12th legendary (pity),
- what is inside is decided once, when the chest is opened, and saved; reloading changes
  nothing, and nothing is ever bought with real money,
- chests never expire, and there is nothing to lose.

Other parts of FRITS can give a chest, too (the Steuer-Quest gives one for the dragon):
truhe_geben(conn, grund) gives at most one chest per `grund`.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from frits import store
from frits.spiel import ausbau, stand
from frits.spiel import gewuerze as gw

KARTEN_PRO_TRUHE = 5
GLUECK = 1 / 8  # the chance that a paperwork card is a Glückskarte
# Rarity of what is inside, in percent. Shown to Fritz exactly like this.
CHANCEN = (("gewöhnlich", 60), ("selten", 30), ("legendär", 10))
SELTEN_SPAETESTENS = 4  # at the latest every 4th chest is rare or better
LEGENDAER_SPAETESTENS = 12  # at the latest every 12th chest is legendary
STUFE = {"gewöhnlich": 0, "selten": 1, "legendär": 2}
# Only real paperwork fills a chest.
ZAEHLT = gw.PAPIERKRAM | {"steuer", "steuerjahr"}
WILLKOMMEN = "willkommen"

# What a chest can hold, by rarity. Körner grow with the house like prices do.
KOERNER = {"gewöhnlich": 8, "selten": 20, "legendär": 50}
JOKER = {"gewöhnlich": 4, "selten": 8, "legendär": 25}  # Körner of a Jokerkarte
WUERZE = {"gewöhnlich": 0.5, "selten": 1.0, "legendär": 2.0}
GOLD = {"selten": 8, "legendär": 15}  # for the Steuer-Quest (a helper costs 8 to 18)
WUERZE_HOECHSTENS = 3.0  # chest Würze waiting for the next Kassensturz at most
# How often each kind of find comes up within its rarity. Things to keep come first.
GEWICHTE = {"ding": 3, "gewuerz": 3, "joker": 2, "wuerze": 2, "gold": 2, "koerner": 1}


@dataclass
class Truhe:
    id: int
    grund: str
    text: str
    mindestens: str | None
    erhalten: str
    geoeffnet: str | None
    inhalt: dict | None


def _truhe(row) -> Truhe:
    return Truhe(
        id=row[0], grund=row[1], text=row[2], mindestens=row[3], erhalten=row[4],
        geoeffnet=row[5], inhalt=json.loads(row[6]) if row[6] else None,
    )  # fmt: skip


SPALTEN = "id, grund, text, mindestens, erhalten, geoeffnet, inhalt"


def truhe_geben(
    conn: sqlite3.Connection,
    grund: str,
    seltenheit: str | None = None,
    text: str = "",
    heute: date | None = None,
) -> bool:
    """Give Fritte one chest for `grund` (e.g. "drachensieg:2025"). At most one per grund,
    so calling it twice is harmless. `seltenheit` is the least it holds. True if new."""
    stand.vorbereiten(conn)
    if seltenheit not in STUFE:
        seltenheit = None
    cur = conn.execute(
        "INSERT OR IGNORE INTO spiel_truhen (grund, text, mindestens, erhalten)"
        " VALUES (?, ?, ?, ?)",
        (grund, text or "Eine Truhe", seltenheit, (heute or date.today()).isoformat()),
    )
    return cur.rowcount == 1


def geschlossen(conn) -> list[Truhe]:
    stand.vorbereiten(conn)
    return [
        _truhe(r)
        for r in conn.execute(
            f"SELECT {SPALTEN} FROM spiel_truhen WHERE geoeffnet IS NULL ORDER BY id"
        ).fetchall()
    ]


def geoeffnete(conn, limit: int = 8) -> list[Truhe]:
    stand.vorbereiten(conn)
    return [
        _truhe(r)
        for r in conn.execute(
            f"SELECT {SPALTEN} FROM spiel_truhen WHERE geoeffnet IS NOT NULL"
            " ORDER BY reihe DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]


def laden(conn, tid: int) -> Truhe | None:
    stand.vorbereiten(conn)
    row = conn.execute(f"SELECT {SPALTEN} FROM spiel_truhen WHERE id = ?", (tid,)).fetchone()
    return _truhe(row) if row else None


def fuellung(conn) -> int:
    return int(stand.einstellung(conn, "truhe_fuellung", "0") or 0)


def ist_glueck(schluessel: str) -> bool:
    """Decided by the card itself, once: the same card is always lucky or never."""
    return random.Random(f"glueck/{schluessel}").random() < GLUECK


def fuellen(conn, heute: date, nummer: int, karten: list, willkommen: bool) -> dict:
    """After a Kassensturz (inside its transaction): paperwork cards fill the chest,
    Glückskarten bring one of their own. Returns what to show on the result page."""
    neu = []
    if truhe_geben(
        conn, WILLKOMMEN, text="Fritte hat eine Truhe für dich gefunden. Mach sie auf!",
        heute=heute,
    ):  # fmt: skip
        neu.append("Fritte hat eine Truhe für dich gefunden")
    vorher = fuellung(conn)
    if willkommen:  # old paperwork is a present already, it doesn't fill chests
        return {
            "vorher": vorher, "dazu": 0, "jetzt": vorher, "neu": neu, "anzahl": len(neu),
            "glueck": [],
        }  # fmt: skip
    zaehlt = [k for k in karten if k.art in ZAEHLT]
    glueck = []
    for i, k in enumerate(karten):
        if not (k.art in ZAEHLT and ist_glueck(k.schluessel)):
            continue
        if truhe_geben(conn, f"glueck:{k.schluessel}", text=f"Glückskarte: {k.titel}", heute=heute):
            glueck.append(i)
            neu.append(f"Glückskarte: {k.titel}")
    gesamt = vorher + len(zaehlt)
    volle = gesamt // KARTEN_PRO_TRUHE
    for _ in range(volle):
        serie = int(stand.einstellung(conn, "truhe_serie", "0") or 0) + 1
        stand.setze(conn, "truhe_serie", str(serie))
        truhe_geben(
            conn, f"voll:{serie}", text=f"Volle Truhe aus dem Kassensturz Nr. {nummer}",
            heute=heute,
        )  # fmt: skip
    if volle:
        neu.append("Die Truhe ist voll" if volle == 1 else f"{volle} volle Truhen")
    jetzt = gesamt % KARTEN_PRO_TRUHE
    stand.setze(conn, "truhe_fuellung", str(jetzt))
    return {
        "vorher": vorher, "dazu": len(zaehlt), "jetzt": jetzt, "neu": neu, "glueck": glueck,
        "anzahl": len(neu) - (1 if volle else 0) + volle,
    }  # fmt: skip


# ---------- opening ----------


def _seit(conn, stufe: int) -> int:
    """How many chests were opened since the last one of at least this rarity."""
    letzte = conn.execute(
        "SELECT MAX(reihe) FROM spiel_truhen WHERE geoeffnet IS NOT NULL AND stufe >= ?",
        (stufe,),
    ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM spiel_truhen WHERE geoeffnet IS NOT NULL AND reihe > ?",
        (letzte or 0,),
    ).fetchone()[0]


def chancen(conn) -> dict:
    """What the page tells Fritz before he opens one."""
    stand.vorbereiten(conn)
    return {
        "prozent": CHANCEN,
        "selten_in": SELTEN_SPAETESTENS - _seit(conn, 1),
        "legendaer_in": LEGENDAER_SPAETESTENS - _seit(conn, 2),
        "geoeffnet": conn.execute(
            "SELECT COUNT(*) FROM spiel_truhen WHERE geoeffnet IS NOT NULL"
        ).fetchone()[0],
    }


def _seltenheit(conn, rng: random.Random, mindestens: str | None) -> str:
    wurf, summe = rng.random() * 100, 0
    gezogen = CHANCEN[0][0]
    for name, prozent in CHANCEN:
        summe += prozent
        if wurf < summe:
            gezogen = name
            break
    stufe = max(STUFE[gezogen], STUFE.get(mindestens or "", 0))
    if _seit(conn, 2) + 1 >= LEGENDAER_SPAETESTENS:
        stufe = 2
    elif _seit(conn, 1) + 1 >= SELTEN_SPAETESTENS:
        stufe = max(stufe, 1)
    return next(name for name, s in STUFE.items() if s == stufe)


def ding_seltenheit(ding) -> str:
    if ding.truhe:
        return ding.truhe
    return "gewöhnlich" if ding.preis <= 15 else "selten" if ding.preis <= 35 else "legendär"


def _faktor(conn) -> float:
    return 1 + 0.5 * (ausbau.preisstufe(conn) - 1)


def truhenwuerze(conn) -> float:
    return float(stand.einstellung(conn, "truhen_wuerze", "0") or 0)


def _fund(conn, rng: random.Random, seltenheit: str, gold: bool, sachen: bool = False) -> dict:
    from frits import haustier  # haustier imports the game modules, so only here
    from frits.spiel import kassensturz

    pool: list[tuple[int, dict]] = []
    sache = 2 if seltenheit == "legendär" else 1  # a legendary chest leans to things to keep
    dinge = sorted(
        d.id for d in haustier.DINGE.values()
        if ding_seltenheit(d) == seltenheit and not stand.besitzt(conn, d.id)
    )  # fmt: skip
    if dinge:
        pool.append((GEWICHTE["ding"] * sache, {"art": "ding", "id": rng.choice(dinge)}))
    if len(kassensturz.regal(conn)) < kassensturz.plaetze(conn):
        frei = [g.id for g in gw.GEWUERZE if g.seltenheit == seltenheit]
        frei = [i for i in frei if not stand.besitzt(conn, i)]
        if frei:
            pool.append((GEWICHTE["gewuerz"] * sache, {"art": "gewuerz", "id": rng.choice(frei)}))
    pool.append((GEWICHTE["joker"], {"art": "joker", "wert": JOKER[seltenheit]}))
    if truhenwuerze(conn) + WUERZE[seltenheit] <= WUERZE_HOECHSTENS:
        pool.append((GEWICHTE["wuerze"], {"art": "wuerze", "wert": WUERZE[seltenheit]}))
    if gold and seltenheit in GOLD:
        pool.append((GEWICHTE["gold"], {"art": "gold", "wert": GOLD[seltenheit]}))
    pool.append(
        (
            GEWICHTE["koerner"],
            {"art": "koerner", "wert": round(KOERNER[seltenheit] * _faktor(conn))},
        )
    )
    if sachen and any(f["art"] in ("ding", "gewuerz") for _, f in pool):
        pool = [(w, f) for w, f in pool if f["art"] in ("ding", "gewuerz")]
    wurf, summe = rng.random() * sum(w for w, _ in pool), 0.0
    for w, fund in pool:
        summe += w
        if wurf < summe:
            return fund
    return pool[-1][1]


def oeffnen(conn: sqlite3.Connection, heute: date, gold_jahr: int | None = None) -> Truhe | None:
    """Open the oldest closed chest. What is inside is decided now, once, and saved.
    `gold_jahr`: the Steuer-Quest year gold would go to, None if there is no quest."""
    from frits import haustier
    from frits.spiel import kassensturz

    stand.vorbereiten(conn)
    with store.transaction(conn):
        row = conn.execute(
            f"SELECT {SPALTEN} FROM spiel_truhen WHERE geoeffnet IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        t = _truhe(row)
        rng = random.Random(f"truhe/{t.id}/{t.grund}")
        seltenheit = _seltenheit(conn, rng, t.mindestens)
        # The very first chest shows what chests are about: something to keep.
        fund = _fund(conn, rng, seltenheit, gold_jahr is not None, t.grund == WILLKOMMEN)
        tag = heute.isoformat()
        art = fund["art"]
        if art == "ding":
            ding = haustier.DINGE[fund["id"]]
            stand.nimm(conn, ding.id, "ding", tag, 0)
            if ding.art == "hut" and not stand.einstellung(conn, "hut"):
                stand.setze(conn, "hut", ding.id)
            fund.update(name=ding.name, zeichen=ding.zeichen, text=ding.text, wo=ding.art)
        elif art == "gewuerz":
            g = gw.NACH_ID[fund["id"]]
            stand.nimm(conn, g.id, "gewuerz", tag, g.preis)
            stand.setze(conn, f"reife:{g.id}", "0")
            stand.setze(conn, f"reifewoche:{g.id}", "")
            fund.update(name=g.name, zeichen=g.zeichen, text=g.text)
            if len(kassensturz.regal(conn)) > kassensturz.plaetze(conn):  # can't happen
                raise stand.SpielFehler("Das Regal ist voll.")
        elif art == "joker":
            conn.execute(
                "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen)"
                " VALUES (?, 'joker', ?)",
                (f"joker:{t.id}", tag),
            )
            fund.update(name="Jokerkarte", zeichen="🃏",
                        text=f"Liegt beim nächsten Kassensturz mit +{fund['wert']} Körnern"
                        " auf dem Tisch. Die Würze zählt mit.")  # fmt: skip
        elif art == "wuerze":
            stand.setze(conn, "truhen_wuerze", f"{truhenwuerze(conn) + fund['wert']:g}")
            fund.update(name="Glas Truhenwürze", zeichen="✨",
                        text=f"+{gw._zahl(fund['wert'])} Würze beim nächsten Kassensturz.")  # fmt: skip
        elif art == "gold":
            fund.update(name=f"{fund['wert']} Drachengold", zeichen="🪙", jahr=gold_jahr,
                        gutgeschrieben=False,
                        text=f"Für die Steuer-Quest {gold_jahr}: damit holst du dir dort Helfer.")  # fmt: skip
        else:
            stand.buche(conn, tag, fund["wert"], "truhe", f"Truhe Nr. {t.id}")
            fund.update(name=f"{fund['wert']} Salzkörner", zeichen="🧂",
                        text="Gleich in Frittes Kasse.")  # fmt: skip
        fund["seltenheit"] = seltenheit
        reihe = conn.execute("SELECT COALESCE(MAX(reihe), 0) + 1 FROM spiel_truhen").fetchone()[0]
        conn.execute(
            "UPDATE spiel_truhen SET geoeffnet = ?, reihe = ?, stufe = ?, inhalt = ?"
            " WHERE id = ? AND geoeffnet IS NULL",
            (tag, reihe, STUFE[seltenheit], json.dumps(fund, ensure_ascii=False), t.id),
        )
    return laden(conn, t.id)


def gold_buchen(conn, t: Truhe, gutschreiben: Callable[..., int] | None) -> None:
    """Gold goes to the Steuer-Quest's own gold book, after the chest is saved (that
    function keeps its own books). Once per chest, however often this runs."""
    fund = t.inhalt or {}
    if fund.get("art") != "gold" or fund.get("gutgeschrieben") or gutschreiben is None:
        return
    try:
        gutschreiben(
            conn, fund["jahr"], fund["wert"], f"Aus Frittes Truhe Nr. {t.id}",
            f"fritte-truhe:{t.id}",
        )  # fmt: skip
    except Exception:  # noqa: BLE001  the quest may not be set up; try again next time
        return
    fund["gutgeschrieben"] = True
    t.inhalt = fund
    conn.execute(
        "UPDATE spiel_truhen SET inhalt = ? WHERE id = ?",
        (json.dumps(fund, ensure_ascii=False), t.id),
    )


def joker_wert(conn, schluessel: str) -> int | None:
    key = schluessel.split(":", 1)[1]
    if not key.isdigit():
        return None
    t = laden(conn, int(key))
    if t is None or not t.inhalt or t.inhalt.get("art") != "joker":
        return None
    return int(t.inhalt["wert"])
