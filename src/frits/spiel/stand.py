"""The game's own state, in its own tables in frits.db.

The bookkeeping tables are only read. Every finished piece of paperwork becomes a card
(spiel_karten) the first time FRITS notices it. Salzkörner live in a small ledger
(spiel_kasse) — Fritte keeps her books properly too: every grain in or out is one line.
"""

from __future__ import annotations

import random
import sqlite3
import tomllib
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from frits import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS spiel_karten (
    schluessel TEXT PRIMARY KEY,   -- e.g. "rechnung:2026-0007", "feierabend:2026-11-01"
    art TEXT NOT NULL,
    gesehen TEXT NOT NULL,         -- the day FRITS first noticed it
    kassensturz INTEGER            -- NULL: waiting; 0: from before the game; else spiel_kassenstuerze.id
);
CREATE TABLE IF NOT EXISTS spiel_kasse (
    id INTEGER PRIMARY KEY,
    tag TEXT NOT NULL,
    betrag INTEGER NOT NULL,       -- + earned, - spent
    art TEXT NOT NULL,             -- start | kassensturz | kauf | verkauf | wurf
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spiel_kassenstuerze (
    id INTEGER PRIMARY KEY,
    tag TEXT NOT NULL,
    summe INTEGER NOT NULL,
    ergebnis TEXT NOT NULL         -- the whole scoring as JSON, for the show and the Chronik
);
CREATE TABLE IF NOT EXISTS spiel_besitz (
    id TEXT PRIMARY KEY,           -- a Ding or a Gewürz id
    art TEXT NOT NULL,             -- ding | gewuerz
    gekauft TEXT NOT NULL,
    preis INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS spiel_einstellung (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spiel_markt (
    woche TEXT PRIMARY KEY,        -- ISO week, e.g. "2026-W44"
    wuerfe INTEGER NOT NULL DEFAULT 0
);
"""

# Base Salzkörner of each kind of card.
GRUND = {
    "rechnung": 3,  # an invoice got its number
    "bezahlt": 2,  # a payment was recorded
    "angebot": 2,
    "erinnerung": 1,  # a friendly payment reminder
    "ausgabe": 1,
    "feierabend": 1,  # the shutdown ritual, once a day
    "steuer": 1,  # a piece collected in the Steuer-Quest
    "steuerjahr": 25,  # the whole tax year done: the big one, once a year
    "start": 3,  # Fritte moves in (only once, in the first Kassensturz)
}
ARTEN_TEXT = {
    "rechnung": "Rechnung geschrieben",
    "bezahlt": "Zahlung eingetragen",
    "angebot": "Angebot geschrieben",
    "erinnerung": "Zahlungserinnerung",
    "ausgabe": "Ausgabe eingetragen",
    "feierabend": "Feierabend gemacht (einmal am Tag)",
    "steuer": "Steuer-Unterlage gesammelt",
    "steuerjahr": "Steuerjahr in der Steuer-Quest geschafft",
}
# Where each kind of card comes from. Storno invoices are not cards.
# Expenses and reminders have their own rules below.
QUELLEN = {
    "rechnung": "SELECT number FROM invoices WHERE cancels IS NULL",
    "bezahlt": "SELECT number FROM invoices WHERE paid_date IS NOT NULL AND cancels IS NULL",
    "angebot": "SELECT number FROM quotes",
    "steuer": "SELECT id FROM steuer_stuecke WHERE status = 'gesammelt'",
    "steuerjahr": "SELECT DISTINCT jahr FROM steuer_chronik WHERE art = 'sieg'",
}
WILLKOMMEN_KARTEN = 25  # up to this many old cards are played as the first Kassensturz
STARTGEWUERZ = "pfeffer"  # Fritte's present when she moves in
ERINNERUNG_ABSTAND = 7  # at most one reminder card per invoice and week


class SpielFehler(Exception):
    """Something the player can't do right now; the message says why, kindly."""


def rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # e.g. no expenses table yet
        return []


def _statements() -> list[str]:
    plain = "\n".join(line.split("--")[0] for line in SCHEMA.splitlines())
    return [sql for sql in plain.split(";") if sql.strip()]


STATEMENTS = _statements()


def vorbereiten(conn: sqlite3.Connection) -> None:
    # One statement at a time: executescript would commit an open transaction.
    for sql in STATEMENTS:
        conn.execute(sql)


def einstellung(conn, schluessel: str, standard: str = "") -> str:
    row = conn.execute(
        "SELECT wert FROM spiel_einstellung WHERE schluessel = ?", (schluessel,)
    ).fetchone()
    return row[0] if row else standard


def setze(conn, schluessel: str, wert: str) -> None:
    conn.execute(
        "INSERT INTO spiel_einstellung (schluessel, wert) VALUES (?, ?)"
        " ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
        (schluessel, wert),
    )


def _gefunden(conn) -> list[tuple[str, str]]:
    found = []
    for art, sql in QUELLEN.items():
        found += [(f"{art}:{value}", art) for (value,) in rows(conn, sql)]
    found += [(key, "ausgabe") for key in ausgaben(conn)]
    found += [(f"erinnerung:{rid}", "erinnerung") for rid in erinnerungen(conn)]
    return found


def ausgabe_inhalt(day, vendor, gross) -> str:
    try:
        amount = f"{Decimal(str(gross)):.2f}"
    except (InvalidOperation, ValueError):
        amount = str(gross)
    return f"{str(day)[:10]}|{' '.join(str(vendor).split()).casefold()}|{amount}"


def ausgaben(conn) -> dict[str, int]:
    """Expense cards by what was paid (day, shop, amount), not by row id: an expense deleted
    and entered again is the same expense, and never a second card. Two identical receipts
    (two bus tickets) are still two cards."""
    seen: Counter = Counter()
    keys = {}
    for rid, day, vendor, gross in rows(
        conn, "SELECT id, date, vendor, gross FROM expenses ORDER BY id"
    ):
        inhalt = ausgabe_inhalt(day, vendor, gross)
        seen[inhalt] += 1
        keys[f"ausgabe:{inhalt}#{seen[inhalt]}"] = int(rid)
    return keys


def erinnerungen(conn) -> list[int]:
    """Reminders that count as cards: one per invoice and week. Clicking four times in a
    row is no reason for Fritte to be pleased; a friendly nudge now and then is."""
    letzte: dict[str, date] = {}
    zaehlt = []
    for rid, number, created in rows(
        conn, "SELECT id, invoice_number, created FROM reminders ORDER BY created, id"
    ):
        try:
            day = date.fromisoformat(str(created)[:10])
        except ValueError:
            continue
        if number in letzte and day - letzte[number] < timedelta(days=ERINNERUNG_ABSTAND):
            continue
        letzte[number] = day
        zaehlt.append(int(rid))
    return zaehlt


def _neue_schluessel(conn) -> None:
    """Expense cards used to be keyed by row id; move them to the content key once."""
    if einstellung(conn, "schluessel_v2"):
        return
    for key, rid in ausgaben(conn).items():
        conn.execute(
            "UPDATE OR IGNORE spiel_karten SET schluessel = ? WHERE schluessel = ?",
            (key, f"ausgabe:{rid}"),
        )
    setze(conn, "schluessel_v2", "1")


def abgleichen(conn: sqlite3.Connection, heute: date, alte_datei: Path | None = None) -> int:
    """Turn new paperwork into waiting cards. Returns how many cards are waiting.

    The very first time, everything already done becomes a welcome gift instead
    (and what the first version of the pet kept in fritte.toml moves in here)."""
    vorbereiten(conn)
    tag = heute.isoformat()
    with store.transaction(conn):
        if not einstellung(conn, "eingerichtet"):
            _einrichten(conn, tag, alte_datei)
        else:
            _neue_schluessel(conn)
            conn.executemany(
                "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, ?, ?)",
                [(key, art, tag) for key, art in _gefunden(conn)],
            )
    return wartende(conn)


def _einrichten(conn, tag: str, alte_datei: Path | None) -> None:
    """The first time. A short history becomes the first Kassensturz, with a jar of pepper
    as a present, so the very first round already shows what Würze does. A long history
    (or the first version's file) becomes a welcome gift instead: nobody wants to watch
    two hundred cards."""
    found = _gefunden(conn)
    alt = _alte_datei(alte_datei)
    if not alt and len(found) < WILLKOMMEN_KARTEN:
        conn.executemany(
            "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, ?, ?)",
            # No day for the old cards: they are not today's work, and no "zeitnah" either.
            [(key, art, "") for key, art in found] + [("start:1", "start", tag)],
        )
        nimm(conn, STARTGEWUERZ, "gewuerz", tag, 0)
        setze(conn, "willkommen", "kassensturz")
        setze(conn, "schluessel_v2", "1")
        setze(conn, "eingerichtet", tag)
        return
    conn.executemany(
        "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen, kassensturz)"
        " VALUES (?, ?, ?, 0)",
        [(key, art, tag) for key, art in found],
    )
    start = sum(GRUND[art] for _, art in found) + GRUND["feierabend"] * int(
        alt.get("feierabende", 0) or 0
    )
    if start:
        buche(conn, tag, start, "start", "Willkommensgeschenk für deinen bisherigen Papierkram")
    setze(conn, "willkommen", "geschenk")
    setze(conn, "schluessel_v2", "1")
    for ding in alt.get("gekauft", []):
        conn.execute(
            "INSERT OR IGNORE INTO spiel_besitz (id, art, gekauft, preis) VALUES (?, 'ding', ?, 0)",
            (str(ding), tag),
        )
    spent = int(alt.get("ausgegeben", 0) or 0)
    if spent:
        buche(conn, tag, -spent, "kauf", "Einkäufe aus der ersten Version")
    if alt.get("traegt"):
        setze(conn, "hut", str(alt["traegt"]))
    for day in alt.get("pflegetage", []):
        conn.execute(
            "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen, kassensturz)"
            " VALUES (?, 'pflege', ?, -1)",
            (f"pflege:{day}", str(day)),
        )
    setze(conn, "eingerichtet", tag)


def _alte_datei(path: Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return {}


def feierabend(conn: sqlite3.Connection, heute: date) -> bool:
    """The shutdown ritual gives one card a day. True if today's is new."""
    vorbereiten(conn)
    cur = conn.execute(
        "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, 'feierabend', ?)",
        (f"feierabend:{heute.isoformat()}", heute.isoformat()),
    )
    return cur.rowcount == 1


def wartende(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM spiel_karten WHERE kassensturz IS NULL AND art != 'pflege'"
    ).fetchone()[0]


def pflegetage(conn, heute: date) -> set[date]:
    """Days with paperwork: from the bookkeeping itself, plus the days cards were noticed
    (an expense has only its payment date, not the day it was entered)."""
    days = set()
    for sql in (
        "SELECT issue_date FROM invoices",
        "SELECT paid_date FROM invoices",
        "SELECT emailed FROM invoices",
        "SELECT created FROM quotes",
        "SELECT created FROM reminders",
        "SELECT received FROM jobs",
        (
            "SELECT gesehen FROM spiel_karten"
            " WHERE (kassensturz IS NULL OR kassensturz != 0) AND art != 'start'"
        ),
    ):
        for (value,) in rows(conn, sql):
            try:
                days.add(date.fromisoformat(str(value)[:10]))
            except (TypeError, ValueError):
                continue
    return {d for d in days if d <= heute}


# ---------- the Salzkorn ledger ----------


def buche(conn, tag: str, betrag: int, art: str, text: str) -> None:
    conn.execute(
        "INSERT INTO spiel_kasse (tag, betrag, art, text) VALUES (?, ?, ?, ?)",
        (tag, int(betrag), art, text),
    )


def guthaben(conn) -> int:
    vorbereiten(conn)
    return int(conn.execute("SELECT COALESCE(SUM(betrag), 0) FROM spiel_kasse").fetchone()[0])


def kassenbuch(conn, limit: int = 30) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT tag, betrag, art, text FROM spiel_kasse ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    ]


def bezahlen(conn, tag: str, preis: int, text: str) -> None:
    """Spend grains; must run inside a transaction so two clicks can't both pay."""
    if guthaben(conn) < preis:
        fehlen = preis - max(0, guthaben(conn))
        raise SpielFehler(f"Dafür fehlen noch {fehlen} Salzkörner. Die kommen ganz von allein.")
    buche(conn, tag, -preis, "kauf", text)


# ---------- what Fritte owns ----------


def besitz(conn, art: str) -> list[str]:
    vorbereiten(conn)
    return [
        r[0]
        for r in conn.execute(
            "SELECT id FROM spiel_besitz WHERE art = ? ORDER BY rowid", (art,)
        ).fetchall()
    ]


def besitz_preise(conn) -> dict[str, int]:
    """What was paid for each thing Fritte owns (selling gives back half of that)."""
    vorbereiten(conn)
    return {r[0]: int(r[1]) for r in conn.execute("SELECT id, preis FROM spiel_besitz")}


def besitzt(conn, ding_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM spiel_besitz WHERE id = ?", (ding_id,)).fetchone() is not None
    )


def nimm(conn, ding_id: str, art: str, tag: str, preis: int) -> None:
    conn.execute(
        "INSERT INTO spiel_besitz (id, art, gekauft, preis) VALUES (?, ?, ?, ?)",
        (ding_id, art, tag, preis),
    )


def gib_ab(conn, ding_id: str) -> int:
    row = conn.execute("SELECT preis FROM spiel_besitz WHERE id = ?", (ding_id,)).fetchone()
    if row is None:
        raise SpielFehler("Das hat Fritte gar nicht.")
    conn.execute("DELETE FROM spiel_besitz WHERE id = ?", (ding_id,))
    return int(row[0])


# ---------- the weekly market ----------


def woche(heute: date) -> str:
    year, week, _ = heute.isocalendar()
    return f"{year}-W{week:02}"


def wuerfe(conn, heute: date) -> int:
    vorbereiten(conn)
    row = conn.execute("SELECT wuerfe FROM spiel_markt WHERE woche = ?", (woche(heute),)).fetchone()
    return int(row[0]) if row else 0


def angebot(ids: list[str], gewichte: list[float], heute: date, wurf: int, n: int) -> list[str]:
    """The same picks all week long, until Fritz rolls again. No randomness he can't see."""
    rng = random.Random(f"{woche(heute)}/{wurf}")
    pool = list(zip(ids, gewichte, strict=True))
    picked = []
    while pool and len(picked) < n:
        total = sum(w for _, w in pool)
        r, acc = rng.random() * total, 0.0
        for i, (item, w) in enumerate(pool):
            acc += w
            if r <= acc:
                picked.append(item)
                pool.pop(i)
                break
    return picked


def neu_wuerfeln(conn, heute: date, preis: int) -> None:
    with store.transaction(conn):
        bezahlen(conn, heute.isoformat(), preis, "Markt neu gewürfelt")
        conn.execute(
            "INSERT INTO spiel_markt (woche, wuerfe) VALUES (?, 1)"
            " ON CONFLICT(woche) DO UPDATE SET wuerfe = wuerfe + 1",
            (woche(heute),),
        )
