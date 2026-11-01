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
    "korrektur": 0,  # an invoice or payment that replaces a cancelled one already scored
}
ARTEN_TEXT = {
    "rechnung": "Rechnung geschrieben",
    "bezahlt": "Zahlung eingetragen",
    "angebot": "Angebot angenommen",
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
    # A quote counts once the customer said yes: writing quotes alone is no habit to reward.
    "angebot": "SELECT number FROM quotes WHERE status = 'accepted' OR draft_id IS NOT NULL",
    "steuer": "SELECT id FROM steuer_stuecke WHERE status = 'gesammelt'",
    "steuerjahr": "SELECT DISTINCT jahr FROM steuer_chronik WHERE art = 'sieg'",
}
WILLKOMMEN_KARTEN = 24  # up to this many old cards are played as the first Kassensturz
STARTGEWUERZ = "pfeffer"  # Fritte's present when she moves in
ERINNERUNG_FRIST = 14  # a reminder counts at the earliest two weeks after the invoice
KORREKTUR_TAGE = 30  # a new invoice this soon after a Storno is its correction


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
    """The content key of version 2 (day, shop, amount); only used to move old cards."""
    try:
        amount = f"{Decimal(str(gross)):.2f}"
    except (InvalidOperation, ValueError):
        amount = str(gross)
    return f"{str(day)[:10]}|{' '.join(str(vendor).split()).casefold()}|{amount}"


def _ausgaben_v2(conn) -> dict[str, int]:
    seen: Counter = Counter()
    keys = {}
    for rid, day, vendor, gross in rows(
        conn, "SELECT id, date, vendor, gross FROM expenses WHERE struck IS NULL ORDER BY id"
    ):
        inhalt = ausgabe_inhalt(day, vendor, gross)
        seen[inhalt] += 1
        keys[f"ausgabe:{inhalt}#{seen[inhalt]}"] = int(rid)
    return keys


def ausgaben(conn) -> dict[str, int]:
    """Expense cards, keyed so that fixing a mistake never earns a second card:
    an expense from a receipt file is that file; one typed by hand is its day and shop.
    So deleting an expense and entering it again (even with a corrected amount) is the
    same card. Two purchases at the same shop on the same day are still two cards."""
    seen: Counter = Counter()
    keys = {}
    for rid, day, vendor, source in rows(
        conn,
        "SELECT id, date, vendor, source_file FROM expenses WHERE struck IS NULL ORDER BY id",
    ):
        if source:
            keys.setdefault(f"ausgabe:datei:{source}", int(rid))
            continue
        inhalt = f"{str(day)[:10]}|{' '.join(str(vendor).split()).casefold()}"
        seen[inhalt] += 1
        keys[f"ausgabe:{inhalt}#{seen[inhalt]}"] = int(rid)
    return keys


def erinnerungen(conn) -> list[int]:
    """Reminders that count as cards: only for an invoice that is really overdue (at least
    two weeks after it was written), and the next one only after the new due date the last
    reminder gave. Clicking four times in a row is no reason for Fritte to be pleased."""
    frist: dict[str, date] = {}
    zaehlt = []
    for rid, number, created, new_due, issue, due in rows(
        conn,
        "SELECT r.id, r.invoice_number, r.created, r.new_due_date, i.issue_date, i.due_date"
        " FROM reminders r JOIN invoices i ON i.number = r.invoice_number"
        " ORDER BY r.created, r.id",
    ):
        day, ab = _tag(created), frist.get(number)
        if day is None:
            continue
        if ab is None:
            ab = max(
                d for d in (_tag(due), (_tag(issue) or day) + timedelta(ERINNERUNG_FRIST)) if d
            )
        if day <= ab:
            continue
        frist[number] = _tag(new_due) or day + timedelta(days=7)
        zaehlt.append(int(rid))
    return zaehlt


def _tag(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def korrektur_art(conn, key: str, art: str) -> str:
    """A new invoice (or its payment) for a customer whose scored invoice was cancelled
    shortly before is the correction of that mistake: it becomes a "korrektur" card
    without Körner. A mistake plus its correction never pays more than doing it right."""
    if art not in ("rechnung", "bezahlt"):
        return art
    number = key.split(":", 1)[1]
    r = rows(conn, "SELECT customer, issue_date FROM invoices WHERE number = ?", (number,))
    if not r:
        return art
    customer, issue = r[0]
    issued = _tag(issue)
    storniert = rows(
        conn,
        "SELECT k.schluessel FROM spiel_karten k"
        " JOIN invoices o ON k.schluessel = ? || o.number"
        " JOIN invoices s ON s.cancels = o.number"
        " WHERE k.art = ? AND k.kassensturz > 0 AND o.customer = ? AND s.issue_date <= ?"
        " AND s.issue_date >= ?",
        (
            f"{art}:", art, customer, str(issue)[:10],
            ((issued or date.min) - timedelta(days=KORREKTUR_TAGE)).isoformat(),
        ),
    )  # fmt: skip
    if not storniert:
        return art
    schon = rows(
        conn,
        "SELECT COUNT(*) FROM spiel_karten k JOIN invoices i ON k.schluessel = ? || i.number"
        " WHERE k.art = 'korrektur' AND i.customer = ?",
        (f"{art}:", customer),
    )[0][0]
    return "korrektur" if schon < len(storniert) else art


def _neue_schluessel(conn) -> None:
    """Expense cards were keyed by row id (v1), then by day, shop and amount (v2).
    Move them to today's key once, so no expense becomes a card a second time."""
    if einstellung(conn, "schluessel_v3"):
        return
    neu = {rid: key for key, rid in ausgaben(conn).items()}
    alt = {rid: key for key, rid in _ausgaben_v2(conn).items()}
    for rid, key in neu.items():
        for frueher in (f"ausgabe:{rid}", alt.get(rid)):
            if frueher and frueher != key:
                conn.execute(
                    "UPDATE OR IGNORE spiel_karten SET schluessel = ? WHERE schluessel = ?",
                    (key, frueher),
                )
    setze(conn, "schluessel_v3", "1")


def _neue_karten(conn, tag: str) -> None:
    bekannt = {r[0] for r in conn.execute("SELECT schluessel FROM spiel_karten")}
    for key, art in _gefunden(conn):
        if key not in bekannt:
            conn.execute(
                "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, ?, ?)",
                (key, korrektur_art(conn, key, art), tag),
            )
    # A quote that is not accepted (any more) waits off the table until it is.
    if rows(conn, "SELECT name FROM sqlite_master WHERE name = 'quotes'"):
        conn.execute(
            "DELETE FROM spiel_karten WHERE art = 'angebot' AND kassensturz IS NULL"
            " AND substr(schluessel, 9) NOT IN (SELECT number FROM quotes"
            " WHERE status = 'accepted' OR draft_id IS NOT NULL)"
        )


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
            _neue_karten(conn, tag)
    return wartende(conn)


def _einrichten(conn, tag: str, alte_datei: Path | None) -> None:
    """The first time. A short history becomes the first Kassensturz, with a jar of pepper
    as a present, so the very first round already shows what Würze does. A long history
    (or the first version's file) becomes a welcome gift instead: nobody wants to watch
    two hundred cards."""
    found = _gefunden(conn)
    alt = _alte_datei(alte_datei)
    if not alt:
        # A mix of kinds for the table (round robin), the rest becomes a welcome gift.
        nach_art: dict[str, list[str]] = {}
        for key, art in found:
            nach_art.setdefault(art, []).append(key)
        tisch: list[tuple[str, str]] = []
        while len(tisch) < WILLKOMMEN_KARTEN and any(nach_art.values()):
            for art, keys in nach_art.items():
                if keys and len(tisch) < WILLKOMMEN_KARTEN:
                    tisch.append((keys.pop(), art))
        rest = [(key, art) for art, keys in nach_art.items() for key in keys]
        conn.executemany(
            "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, ?, ?)",
            # No day for the old cards: they are not today's work, and no "zeitnah" either.
            [(key, art, "") for key, art in tisch] + [("start:1", "start", tag)],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen, kassensturz)"
            " VALUES (?, ?, ?, 0)",
            [(key, art, tag) for key, art in rest],
        )
        geschenk = sum(GRUND[art] for _, art in rest)
        if geschenk:
            buche(
                conn, tag, geschenk, "start", f"Willkommensgeschenk für {len(rest)} ältere Vorgänge"
            )
        nimm(conn, STARTGEWUERZ, "gewuerz", tag, 0)
        setze(conn, "willkommen", "kassensturz")
        setze(conn, "schluessel_v3", "1")
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
    setze(conn, "schluessel_v3", "1")
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
            " WHERE (kassensturz IS NULL OR kassensturz != 0)"
            " AND art NOT IN ('start', 'feierabend')"  # a click alone is no paperwork
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
