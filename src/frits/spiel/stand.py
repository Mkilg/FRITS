"""The game's own state, in its own tables in frits.db.

The bookkeeping tables are only read. Every finished piece of paperwork becomes a card
(spiel_karten) the first time FRITS notices it. Salzkörner live in a small ledger
(spiel_kasse) — Fritte keeps her books properly too: every grain in or out is one line.
"""

from __future__ import annotations

import random
import re
import sqlite3
import tomllib
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from frits import gutschriften as gs
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
CREATE TABLE IF NOT EXISTS spiel_korrektur (
    alt TEXT PRIMARY KEY,          -- a scored card whose paperwork was cancelled or struck
    seit TEXT NOT NULL,            -- the day the game noticed (its own date, never typed)
    neu TEXT                       -- the card that became its correction, NULL while open
);
CREATE TABLE IF NOT EXISTS spiel_beutel (
    grund TEXT PRIMARY KEY,        -- e.g. "quest:2025:fund:12": each reason books only once
    tag TEXT NOT NULL,
    betrag INTEGER NOT NULL        -- what was really booked (a minus never goes below zero)
);
CREATE TABLE IF NOT EXISTS spiel_markt (
    woche TEXT PRIMARY KEY,        -- ISO week, e.g. "2026-W44"
    wuerfe INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spiel_truhen (
    id INTEGER PRIMARY KEY,
    grund TEXT NOT NULL UNIQUE,    -- why: "voll:3", "glueck:rechnung:2026-0007", "drachensieg:2025"
    text TEXT NOT NULL,            -- where it came from, for Fritz
    mindestens TEXT,               -- the least rarity it holds, if any
    erhalten TEXT NOT NULL,
    geoeffnet TEXT,                -- NULL while closed
    reihe INTEGER,                 -- 1, 2, 3 ... in the order chests were opened
    stufe INTEGER,                 -- 0 gewöhnlich, 1 selten, 2 legendär
    inhalt TEXT                    -- what was inside, as JSON
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
    "aufgabe": 1,  # a point ticked off "Deine Liste", three a day
    "steuer": 1,  # a piece collected in the Steuer-Quest
    # The whole tax year done. The victory itself pays in the quest (rest gold becomes
    # Salzkörner, a chest, an Orden); here it is one card among others, so the Würze
    # does not multiply the same victory a second time (Lena: 342 salt after day one).
    "steuerjahr": 5,
    "uebung": 1,  # an exercise in that course solved, once each
    "lernen": 2,  # a stage of the course "So funktionieren KI-Agenten" finished, once each
    "start": 3,  # Fritte moves in (only once, in the first Kassensturz)
    "korrektur": 0,  # an invoice or payment that replaces a cancelled one already scored
    "joker": 0,  # a Jokerkarte from one of Fritte's chests (its Körner are in the chest)
}
ARTEN_TEXT = {
    "rechnung": "Rechnung geschrieben oder Gutschrift erfasst",
    "bezahlt": "Zahlung eingetragen",
    "angebot": "Angebot wurde zur Rechnung",
    "erinnerung": "Zahlungserinnerung",
    "ausgabe": "Ausgabe eingetragen",
    "feierabend": "Feierabend gemacht (einmal am Tag)",
    "aufgabe": "Punkt auf deiner Liste abgehakt (drei am Tag)",
    "steuer": "Steuer-Unterlage gesammelt",
    "steuerjahr": "Steuerjahr in der Steuer-Quest geschafft",
    "lernen": "Etappe im Kurs „So funktionieren KI-Agenten“ geschafft",
    "uebung": "Übung im Kurs „So funktionieren KI-Agenten“ gelöst",
}
# Where each kind of card comes from. Storno invoices are not cards.
# Expenses and reminders have their own rules below.
QUELLEN = {
    "rechnung": "SELECT number FROM invoices WHERE cancels IS NULL",
    "bezahlt": "SELECT number FROM invoices WHERE paid_date IS NOT NULL AND cancels IS NULL",
    # A quote counts once it became a real invoice: writing quotes, or clicking
    # "angenommen" and throwing the draft away, is no habit to reward.
    "angebot": "SELECT q.number FROM quotes q JOIN invoices i ON i.draft_id = q.draft_id"
    " WHERE i.cancels IS NULL AND i.number NOT IN"
    " (SELECT cancels FROM invoices WHERE cancels IS NOT NULL)",
    # frits.lernen.GESCHAFFT_PREFIX is "lernen:geschafft:" (17 letters), the stage comes after it
    "lernen": "SELECT substr(schluessel, 18) FROM spiel_einstellung"
    " WHERE schluessel LIKE 'lernen:geschafft:%'",
    # frits.lernen.UEBUNG_PREFIX is "lernen:uebung:" (14 letters), then "<etappe>/<happen>"
    "uebung": "SELECT substr(schluessel, 15) FROM spiel_einstellung"
    " WHERE schluessel LIKE 'lernen:uebung:%'",
}
# Tax cards, each with its tax year. They count only for the years the Steuer-Quest still
# pays salt for (steuer.spiel.gold_jahr): an invented old year fills Fritte's jar no more
# than the quest's own.
STEUER_QUELLEN = {
    "steuer": "SELECT id, jahr FROM steuer_stuecke WHERE status = 'gesammelt'",
    "steuerjahr": "SELECT DISTINCT jahr, jahr FROM steuer_chronik WHERE art = 'sieg'",
}
WILLKOMMEN_KARTEN = 12  # up to this many old cards are played as the first Kassensturz
WILLKOMMEN_GESCHENK = 60  # at most this many Salzkörner for the rest: the journey is the fun
STARTGEWUERZ = "pfeffer"  # Fritte's present when she moves in
ERINNERUNG_FRIST = 14  # a reminder counts at the earliest two weeks after the invoice
KORREKTUR_TAGE = 30  # a new card this close to a Storno or strike can be its correction
KORREKTUR_SPANNE = Decimal("0.2")  # ... if its amount is within 20 % of the old one
FEIERABENDE_PRO_WOCHE = 5  # a weekend without work needs no shutdown ritual
VERWORFEN_TAGE = 7  # a draft thrown away counts as still open for Safran this long


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
    for art, sql in STEUER_QUELLEN.items():
        found += [(f"{art}:{v}", art) for v, jahr in rows(conn, sql) if steuer_zaehlt(jahr)]
    found += [(key, "ausgabe") for key in ausgaben(conn)]
    found += [(f"erinnerung:{rid}", "erinnerung") for rid in erinnerungen(conn)]
    for key, credit in gutschriften(conn).items():
        found.append((f"rechnung:{key}", "rechnung"))
        if credit.paid_date:
            found.append((f"bezahlt:{key}", "bezahlt"))
    return found


def steuer_zaehlt(jahr, heute: date | None = None) -> bool:
    """Does a tax card of this year bring Körner? The same years as in the Steuer-Quest."""
    from frits.steuer.spiel import gold_jahr

    try:
        return gold_jahr(int(jahr), heute or date.today())
    except (TypeError, ValueError):
        return False


GS = "gs:"  # invoice numbers never start like this


def gs_schluessel(customer_id: str, their_number: str) -> str:
    return f"{GS}{customer_id}|{' '.join(their_number.split()).casefold()}"


def gutschriften(conn) -> dict[str, gs.Credit]:
    """Gutschriften that count (not struck), by the key part of their cards.

    A Gutschrift is the customer's own invoice: it counts like a Rechnung, and its payment
    like a Zahlung. The key is the customer and their number, not the row: striking one
    and entering it again (say with the amount fixed) is still the same card."""
    return {gs_schluessel(c.customer_id, c.their_number): c for c in gs.listing(conn)}


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


def _stornierte(conn) -> set[str]:
    return {n for (n,) in rows(conn, "SELECT cancels FROM invoices WHERE cancels IS NOT NULL")}


def _betrag(conn, key: str) -> Decimal | None:
    """The amount behind a rechnung or bezahlt card, even after a Storno or strike."""
    art, rest = key.split(":", 1)
    if art not in ("rechnung", "bezahlt"):
        return None
    if rest.startswith(GS):
        for cid, their, gross in rows(
            conn, "SELECT customer_id, their_number, gross FROM credits ORDER BY id DESC"
        ):
            if gs_schluessel(cid, their) == rest:
                return Decimal(str(gross))
        return None
    r = rows(conn, "SELECT gross FROM invoices WHERE number = ?", (rest,))
    try:
        return Decimal(str(r[0][0])) if r else None
    except (InvalidOperation, ValueError):
        return None


def _basis(key: str) -> str:
    return key.split(":", 1)[0]


def korrekturen(conn, heute: date) -> None:
    """A mistake plus its correction never pays more than doing it right.

    When a card that was already scored loses its paperwork (an invoice cancelled, a
    Gutschrift or an expense struck), the next card of the same kind close to that day
    becomes its correction, a card without Körner. For invoices and Gutschriften it must
    also have a similar amount, so new work for the same customer stays a normal card.
    The order does not matter: writing the right one first and cancelling the wrong one
    afterwards ends the same way. It is checked again until the card is scored."""
    gefunden = _gefunden(conn)
    weg = _stornierte(conn)
    lebendig = {
        key for key, art in gefunden
        if art not in ("rechnung", "bezahlt") or key.split(":", 1)[1] not in weg
    }  # fmt: skip
    tag = heute.isoformat()
    for (key,) in rows(
        conn,
        "SELECT schluessel FROM spiel_karten WHERE kassensturz > 0"
        " AND art IN ('rechnung', 'bezahlt', 'ausgabe')"
        " AND schluessel NOT IN (SELECT alt FROM spiel_korrektur)",
    ):
        if key not in lebendig:
            conn.execute("INSERT INTO spiel_korrektur (alt, seit) VALUES (?, ?)", (key, tag))
    # Waiting cards are sorted out afresh each time; scored ones stay as they were.
    conn.execute(
        "UPDATE spiel_korrektur SET neu = NULL WHERE neu IN (SELECT schluessel FROM"
        " spiel_karten WHERE kassensturz IS NULL OR kassensturz = -2)"
    )
    for (key,) in rows(
        conn,
        "SELECT schluessel FROM spiel_karten WHERE art = 'korrektur' AND kassensturz IS NULL",
    ):
        conn.execute("UPDATE spiel_karten SET art = ? WHERE schluessel = ?", (_basis(key), key))
    wartend = [
        (key, art, _tag(gesehen))
        for key, art, gesehen in rows(
            conn,
            "SELECT schluessel, art, gesehen FROM spiel_karten WHERE kassensturz IS NULL"
            " AND art IN ('rechnung', 'bezahlt', 'ausgabe') ORDER BY gesehen, rowid",
        )
        if key in lebendig and _tag(gesehen)
    ]
    vergeben: set[str] = set()
    for alt, seit in rows(
        conn, "SELECT alt, seit FROM spiel_korrektur WHERE neu IS NULL ORDER BY seit, alt"
    ):
        tag_alt = _tag(seit)
        if tag_alt is None:
            continue
        fenster = timedelta(days=KORREKTUR_TAGE)
        art = _basis(alt)
        betrag = _betrag(conn, alt) if art != "ausgabe" else None
        beste, abstand = None, None
        for key, kart, gesehen in wartend:
            if kart != art or key in vergeben or abs(gesehen - tag_alt) > fenster:
                continue
            if art != "ausgabe":
                neu_betrag = _betrag(conn, key)
                if not betrag or neu_betrag is None:
                    continue
                d = abs(neu_betrag - betrag) / betrag
                if d > KORREKTUR_SPANNE:
                    continue
            else:
                d = Decimal(0)
            if abstand is None or d < abstand:
                beste, abstand = key, d
        if beste:
            vergeben.add(beste)
            conn.execute("UPDATE spiel_karten SET art = 'korrektur' WHERE schluessel = ?", (beste,))
            conn.execute("UPDATE spiel_korrektur SET neu = ? WHERE alt = ?", (beste, alt))


def verworfen_merken(conn, heute: date) -> None:
    """Notice when a draft is thrown away (FRITS has no date for that, so the game keeps
    its own): for a week it still counts as open for Safran, so throwing work away never
    pays off."""
    n = rows(conn, "SELECT COUNT(*) FROM drafts WHERE status = 'discarded'")
    n = n[0][0] if n else 0
    if n > int(einstellung(conn, "verworfen_zahl", "0") or 0):
        setze(conn, "verworfen_am", heute.isoformat())
    if str(n) != einstellung(conn, "verworfen_zahl"):
        setze(conn, "verworfen_zahl", str(n))


def kuerzlich_verworfen(conn, heute: date) -> bool:
    am = _tag(einstellung(conn, "verworfen_am"))
    return bool(am and (heute - am).days < VERWORFEN_TAGE)


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
    weg = {r[0] for r in conn.execute("SELECT schluessel FROM spiel_karten WHERE kassensturz = -2")}
    for key, art in _gefunden(conn):
        if key in weg and (art == "ausgabe" or key.split(":", 1)[1].startswith(GS)):
            # Struck out before it was scored, then entered again (or restored): the
            # card was never counted, so it simply waits again. Still only once.
            conn.execute("UPDATE spiel_karten SET kassensturz = NULL WHERE schluessel = ?", (key,))
        elif key not in bekannt:
            conn.execute(
                "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, ?, ?)",
                (key, art, tag),
            )
    # A quote that is no invoice (any more) waits off the table until it is.
    angebote = {f"angebot:{v}" for (v,) in rows(conn, QUELLEN["angebot"])}
    for (key,) in rows(
        conn, "SELECT schluessel FROM spiel_karten WHERE art = 'angebot' AND kassensturz IS NULL"
    ):
        if key not in angebote:
            conn.execute("DELETE FROM spiel_karten WHERE schluessel = ?", (key,))


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
            korrekturen(conn, heute)
        verworfen_merken(conn, heute)
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
        geschenk = min(WILLKOMMEN_GESCHENK, sum(GRUND[art] for _, art in rest))
        if geschenk:
            buche(
                conn, tag, geschenk, "start", f"Willkommensgeschenk für {len(rest)} ältere Vorgänge"
            )
        setze(conn, "willkommen_vorgaenge", str(len(found)))
        setze(conn, "willkommen_geschenk", str(geschenk))
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
    # What the first version already counted is kept as it was (it may have been spent).
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
    """The shutdown ritual gives one card a day, five a week at most. True if today's
    is new."""
    vorbereiten(conn)
    montag = heute - timedelta(days=heute.weekday())
    schon = conn.execute(
        "SELECT COUNT(*) FROM spiel_karten WHERE art = 'feierabend' AND gesehen BETWEEN ? AND ?",
        (montag.isoformat(), (montag + timedelta(days=6)).isoformat()),
    ).fetchone()[0]
    if schon >= FEIERABENDE_PRO_WOCHE:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO spiel_karten (schluessel, art, gesehen) VALUES (?, 'feierabend', ?)",
        (f"feierabend:{heute.isoformat()}", heute.isoformat()),
    )
    return cur.rowcount == 1


def wartende(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM spiel_karten WHERE kassensturz IS NULL"
        " AND art NOT IN ('pflege', 'korrektur')"
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
            " AND art NOT IN ('start', 'feierabend', 'aufgabe', 'lernen', 'joker')"  # no paperwork
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


UMSTELLUNG = "Umstellung auf eine Währung"


def _gruppe(z: dict) -> str | None:
    """Which Kassenbuch rows of one day fold into one: a quest year, or the currency move."""
    if z["art"] == "drachensieg" or (z["art"] == "quest" and "Gold von früher" in z["text"]):
        return UMSTELLUNG
    q = re.match(r"(Steuer-Quest \d{4})", z["text"]) if z["art"] == "quest" else None
    return q.group(1) if q else None


def _teiltext(z: dict) -> str:
    """A single booking inside a bundle, without the "Steuer-Quest 2025: " its head names."""
    return z["text"].removeprefix(f"{z['gruppe']}: ")


def _sammeltext(zeile: dict) -> str:
    """ "Steuer-Quest 2025: 11 Buchungen · Serie bis ×8" (the series if the texts name one)."""
    if zeile["gruppe"] == UMSTELLUNG:
        return UMSTELLUNG
    text = f"{zeile['gruppe']}: {len(zeile['teile'])} Buchungen"
    serien = [int(x) for t in zeile["teile"] for x in re.findall(r"×\s?(\d+)", t["text"])]
    return f"{text} · Serie bis ×{max(serien)}" if serien else text


def kassenbuch(conn, limit: int = 30) -> list[dict]:
    """Every grain in or out, newest first. A Kassensturz row knows its number, so the
    Kassenbuch is also the chronicle: each one can be replayed from there.

    The Steuer-Quest books many small amounts (one per Zettel). Those of one quest year
    on one day become one row with a sum; the single bookings stay in "teile". The same
    goes for the one-time move from the old quest gold to Salzkörner (Lena: one row)."""
    zeilen: list[dict] = []
    sammel: dict[tuple[str, str], dict] = {}  # (gruppe, tag) -> its row
    for r in conn.execute(
        "SELECT tag, betrag, art, text FROM spiel_kasse ORDER BY id DESC LIMIT ?", (limit * 3,)
    ).fetchall():
        z = dict(r)
        m = re.match(r"Kassensturz Nr\. (\d+)", z["text"]) if z["art"] == "kassensturz" else None
        z["nummer"] = int(m.group(1)) if m else None
        z["teile"] = []
        z["gruppe"] = _gruppe(z)
        # Lena: a purchase in between must not split one day's quest into several rows,
        # so a group gathers the whole day and stays where its newest booking is.
        zeile = sammel.get((z["gruppe"], z["tag"])) if z["gruppe"] else None
        if zeile is None:
            zeilen.append(z)
            if z["gruppe"]:
                sammel[(z["gruppe"], z["tag"])] = z
            continue
        if not zeile["teile"]:
            zeile["teile"] = [{"text": _teiltext(zeile), "betrag": zeile["betrag"]}]
        zeile["teile"].append({"text": _teiltext(z), "betrag": z["betrag"]})
        zeile["betrag"] += z["betrag"]
        zeile["text"] = _sammeltext(zeile)
    zeilen = zeilen[:limit]
    # Lena: the window above may cut a long day in half, so a bundle counts its whole day
    for (gruppe, tag), zeile in sammel.items():
        if zeile not in zeilen:
            continue
        teile = []
        for r in conn.execute(
            "SELECT tag, betrag, art, text FROM spiel_kasse WHERE tag = ? ORDER BY id DESC", (tag,)
        ).fetchall():
            z = dict(r)
            if _gruppe(z) == gruppe:
                teile.append({"text": _teiltext({**z, "gruppe": gruppe}), "betrag": z["betrag"]})
        if len(teile) > 1:
            zeile["teile"], zeile["betrag"] = teile, sum(t["betrag"] for t in teile)
            zeile["text"] = _sammeltext(zeile)
    return zeilen


def bezahlen(conn, tag: str, preis: int, text: str) -> None:
    """Spend grains; must run inside a transaction so two clicks can't both pay."""
    if guthaben(conn) < preis:
        fehlen = preis - max(0, guthaben(conn))
        raise SpielFehler(f"Dafür fehlen noch {fehlen} Salzkörner. Die kommen ganz von allein.")
    buche(conn, tag, -preis, "kauf", text)


# ---------- one purse for all of FRITS (Fritte and the Steuer-Quest) ----------
# Marie (2026-11-02): one currency instead of Gold and Salzkörner. The Steuer-Quest keeps
# its own journal (steuer_gold: reasons, years, the Goldbuch) but pays into and out of
# this purse. None of these open a transaction: call them inside the caller's, so the
# quest's journal line and the purse line land together or not at all.


def beutel_gutschreiben(
    conn, grund: str, betrag: int, text: str, tag: str | None = None, art: str = "quest"
) -> int:
    """Book Salzkörner for a reason from outside Fritte's own game, once per reason.

    A minus takes grains back (a quest card put back), but the purse never goes below zero:
    it takes at most what is there. Returns what was really booked: 0 when the reason was
    booked before, otherwise the (possibly smaller) amount, negative for a take-back. The
    caller decides what to do about a shortfall (the quest takes back its last purchase).
    """
    vorbereiten(conn)
    if not grund or conn.execute("SELECT 1 FROM spiel_beutel WHERE grund = ?", (grund,)).fetchone():
        return 0
    betrag = int(betrag)
    if betrag < 0:
        betrag = -min(-betrag, max(0, guthaben(conn)))
    tag = tag or date.today().isoformat()
    conn.execute(
        "INSERT INTO spiel_beutel (grund, tag, betrag) VALUES (?, ?, ?)", (grund, tag, betrag)
    )
    if betrag:
        buche(conn, tag, betrag, art, text)
    return betrag


def beutel_gebucht(conn, grund: str) -> int | None:
    """What a reason booked, or None if it never did."""
    vorbereiten(conn)
    row = conn.execute("SELECT betrag FROM spiel_beutel WHERE grund = ?", (grund,)).fetchone()
    return None if row is None else int(row[0])


def beutel_bezahlen(conn, preis: int, text: str, tag: str | None = None) -> None:
    """Pay from the one purse, e.g. a helper for the Steuer-Quest. Raises SpielFehler with a
    kind sentence when there aren't enough grains (nothing is booked then)."""
    vorbereiten(conn)
    bezahlen(conn, tag or date.today().isoformat(), int(preis), text)


# ---------- what Fritte owns ----------


def zeitstempel(heute: date) -> str:
    """The day the game decided on, with the clock's time: "2026-11-02T14:05:09", the same
    form as the Steuer-Quest's chronicle, so the showcase can tell which came last."""
    return f"{heute.isoformat()}T{datetime.now():%H:%M:%S}"


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
