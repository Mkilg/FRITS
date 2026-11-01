"""Der Kassensturz: the waiting cards are scored, one after the other.

Körner × Würze = Salzkörner. Each card brings its base Körner, the spices on the shelf
add more, then the spices' Würze is applied from left to right, so the order on the
shelf matters. The whole calculation is kept as a list of steps, so the page can play
it back and the Chronik can show it again later.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from frits import store
from frits.spiel import album, ausbau, stand
from frits.spiel.gewuerze import NACH_ID, Gewuerz

ZEITNAH_TAGE = 10  # GoBD: enter non-cash bookings within about ten days
FRISCH_TAGE = 7  # invoice written within a week of the work
GRUND_PLAETZE = 3  # spice shelf places before the house is built out


@dataclass
class Karte:
    schluessel: str
    art: str
    titel: str
    detail: str
    koerner: int
    fakten: dict

    def als_dict(self) -> dict:
        return {
            "art": self.art,
            "titel": self.titel,
            "detail": self.detail,
            "koerner": self.koerner,
            **self.fakten,
        }


def _tag(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", text)
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    year += 2000 if year < 100 else 0
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _euro(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _storniert(conn, number: str) -> bool:
    return bool(stand.rows(conn, "SELECT 1 FROM invoices WHERE cancels = ?", (number,)))


def _leistungstag(conn, draft_id) -> date | None:
    rows = stand.rows(conn, "SELECT data FROM drafts WHERE id = ?", (draft_id,))
    if not rows:
        return None
    try:
        lines = json.loads(rows[0][0]).get("lines", [])
    except (ValueError, AttributeError):
        return None
    days = [d for d in (_tag(line.get("date")) for line in lines) if d]
    return max(days) if days else None


def _karte(conn, schluessel: str, art: str, gesehen: str) -> Karte | None:
    """The card with what the spices need to know, or None if the paperwork is gone."""
    key = schluessel.split(":", 1)[1]
    base = stand.GRUND.get(art, 0)
    if art == "rechnung":
        r = stand.rows(
            conn,
            "SELECT customer, issue_date, gross, draft_id FROM invoices"
            " WHERE number = ? AND cancels IS NULL",
            (key,),
        )
        if not r or _storniert(conn, key):
            return None
        customer, issue, gross, draft_id = r[0]
        work, written = _leistungstag(conn, draft_id), _tag(issue)
        frisch = bool(work and written and 0 <= (written - work).days <= FRISCH_TAGE)
        return Karte(
            schluessel, art, f"Rechnung {key}", f"{customer} · {_euro(gross)}", base,
            {"frisch": frisch},
        )  # fmt: skip
    if art == "bezahlt":
        r = stand.rows(
            conn,
            "SELECT customer, paid_date, due_date, gross FROM invoices"
            " WHERE number = ? AND paid_date IS NOT NULL AND cancels IS NULL",
            (key,),
        )
        if not r or _storniert(conn, key):
            return None
        customer, paid, due, gross = r[0]
        paid_day, due_day = _tag(paid), _tag(due)
        puenktlich = bool(paid_day and due_day and paid_day <= due_day)
        return Karte(
            schluessel, art, "Zahlung eingegangen", f"{key} · {customer} · {_euro(gross)}", base,
            {"puenktlich": puenktlich},
        )  # fmt: skip
    if art == "angebot":
        r = stand.rows(conn, "SELECT created FROM quotes WHERE number = ?", (key,))
        if not r:
            return None
        return Karte(schluessel, art, f"Angebot {key}", "geschrieben", base, {})
    if art == "erinnerung":
        r = stand.rows(
            conn, "SELECT invoice_number, level FROM reminders WHERE id = ?", (int(key),)
        )
        if not r:
            return None
        return Karte(
            schluessel, art, "Zahlungserinnerung", f"Rechnung {r[0][0]} · Stufe {r[0][1]}", base,
            {},
        )  # fmt: skip
    if art == "ausgabe":
        r = stand.rows(conn, "SELECT date, vendor, gross FROM expenses WHERE id = ?", (int(key),))
        if not r:
            return None
        paid, vendor, gross = r[0]
        paid_day, seen = _tag(paid), _tag(gesehen)
        zeitnah = bool(paid_day and seen and -1 <= (seen - paid_day).days <= ZEITNAH_TAGE)
        return Karte(
            schluessel, art, "Ausgabe", f"{vendor} · {_euro(gross)}", base, {"zeitnah": zeitnah}
        )
    if art == "feierabend":
        day = _tag(key)
        return Karte(
            schluessel, art, "Feierabend", day.strftime("%d.%m.%Y") if day else key, base, {}
        )
    return None


def offene_karten(conn: sqlite3.Connection) -> list[Karte]:
    """Waiting cards, oldest first. Cards whose paperwork was deleted or cancelled
    drop out for good: undoing work never earns anything."""
    stand.vorbereiten(conn)
    karten, weg = [], []
    for schluessel, art, gesehen in conn.execute(
        "SELECT schluessel, art, gesehen FROM spiel_karten"
        " WHERE kassensturz IS NULL AND art != 'pflege' ORDER BY gesehen, rowid"
    ).fetchall():
        karte = _karte(conn, schluessel, art, gesehen)
        if karte is None:
            weg.append(schluessel)
        else:
            karten.append(karte)
    if weg:
        conn.executemany(
            "UPDATE spiel_karten SET kassensturz = -2 WHERE schluessel = ?", [(k,) for k in weg]
        )
    return karten


# ---------- the shelf ----------


def plaetze(conn) -> int:
    return GRUND_PLAETZE + ausbau.stufe(conn).plaetze


def regal(conn) -> list[Gewuerz]:
    order = stand.einstellung(conn, "regal")
    owned = stand.besitz(conn, "gewuerz")
    ids = [i for i in order.split(",") if i in owned] if order else []
    ids += [i for i in owned if i not in ids]
    return [NACH_ID[i] for i in ids if i in NACH_ID]


def regal_ordnen(conn, ids: list[str]) -> None:
    stand.setze(conn, "regal", ",".join(ids))


def reife(conn) -> dict[str, int]:
    return {g.id: int(stand.einstellung(conn, f"reife:{g.id}", "0")) for g in regal(conn)}


def rhythmus_wochen(conn, heute: date, wochen: int = 3) -> int:
    days = stand.pflegetage(conn, heute)
    monday = heute - timedelta(days=heute.weekday())
    count = 0
    for n in range(wochen):
        start = monday - timedelta(weeks=n)
        count += any(start <= d < start + timedelta(days=7) for d in days)
    return count


# ---------- scoring ----------


def berechnen(karten: list[dict], gewuerze: list[Gewuerz], umfeld: dict) -> dict:
    """Pure scoring. Returns {"schritte": [...], "koerner", "wuerze", "summe"}."""
    koerner, wuerze, schritte = 0, float(umfeld.get("grundwuerze", 1.0)), []
    for i, karte in enumerate(karten):
        koerner += karte["koerner"]
        schritte.append(
            {"typ": "karte", "karte": i, "plus": karte["koerner"], "koerner": koerner,
             "wuerze": wuerze}
        )  # fmt: skip
        for g in gewuerze:
            extra = g.pro_karte(karte, umfeld) if g.pro_karte else 0
            if extra:
                koerner += extra
                schritte.append(
                    {"typ": "gewuerz", "karte": i, "gewuerz": g.id, "plus": extra,
                     "koerner": koerner, "wuerze": wuerze}
                )  # fmt: skip
    for g in gewuerze:
        if g.plus_wuerze:
            plus = g.plus_wuerze(karten, umfeld)
            if plus:
                wuerze = round(wuerze + plus, 2)
                schritte.append(
                    {"typ": "plus", "gewuerz": g.id, "plus": plus, "koerner": koerner,
                     "wuerze": wuerze}
                )  # fmt: skip
        if g.mal_wuerze:
            mal = g.mal_wuerze(karten, umfeld)
            if mal != 1:
                wuerze = round(wuerze * mal, 2)
                schritte.append(
                    {"typ": "mal", "gewuerz": g.id, "mal": mal, "koerner": koerner,
                     "wuerze": wuerze}
                )  # fmt: skip
    summe = math.floor(koerner * wuerze + 1e-9)
    return {
        "schritte": schritte,
        "koerner": koerner,
        "wuerze": wuerze,
        "summe": summe,
        "grundwuerze": float(umfeld.get("grundwuerze", 1.0)),
    }


def vorschau(conn, heute: date, aufgeraeumt: bool) -> tuple[list[Karte], dict]:
    karten = offene_karten(conn)
    umfeld = {
        "rhythmus_wochen": rhythmus_wochen(conn, heute),
        "aufgeraeumt": aufgeraeumt,
        "reife": reife(conn),
        "grundwuerze": ausbau.stufe(conn).grundwuerze,
    }
    return karten, berechnen([k.als_dict() for k in karten], regal(conn), umfeld)


def durchfuehren(conn: sqlite3.Connection, heute: date, aufgeraeumt: bool) -> int:
    """Score all waiting cards, book the grains, return the Kassensturz id."""
    with store.transaction(conn):
        karten, ergebnis = vorschau(conn, heute, aufgeraeumt)
        if not karten:
            raise stand.SpielFehler(
                "Gerade wartet keine Karte. Erledigter Papierkram wird zur Karte."
            )
        shelf = regal(conn)
        record = {
            **ergebnis,
            "karten": [k.als_dict() for k in karten],
            "gewuerze": [
                {"id": g.id, "name": g.name, "zeichen": g.zeichen, "seltenheit": g.seltenheit,
                 "text": g.text}
                for g in shelf
            ],
        }  # fmt: skip
        cur = conn.execute(
            "INSERT INTO spiel_kassenstuerze (tag, summe, ergebnis) VALUES (?, ?, ?)",
            (heute.isoformat(), ergebnis["summe"], json.dumps(record, ensure_ascii=False)),
        )
        nummer = cur.lastrowid
        conn.executemany(
            "UPDATE spiel_karten SET kassensturz = ? WHERE schluessel = ?",
            [(nummer, k.schluessel) for k in karten],
        )
        n = len(karten)
        stand.buche(
            conn, heute.isoformat(), ergebnis["summe"], "kassensturz",
            f"Kassensturz Nr. {nummer}: {n} {'Karte' if n == 1 else 'Karten'}",
        )  # fmt: skip
        for g in shelf:
            key = f"reife:{g.id}"
            stand.setze(conn, key, str(int(stand.einstellung(conn, key, "0")) + 1))
        fertig = ausbau.fertigstellen(conn)
        if fertig:
            record["ausbau"] = {"name": fertig.name, "zeichen": fertig.zeichen, "text": fertig.text}
        neu = album.neue(conn, heute)
        if neu:
            record["erfolge"] = [
                {"name": e.name, "zeichen": e.zeichen, "text": e.text} for e in neu
            ]
        if fertig or neu:
            conn.execute(
                "UPDATE spiel_kassenstuerze SET ergebnis = ? WHERE id = ?",
                (json.dumps(record, ensure_ascii=False), nummer),
            )
    return nummer


def laden(conn, nummer: int) -> dict | None:
    stand.vorbereiten(conn)
    row = conn.execute(
        "SELECT tag, summe, ergebnis FROM spiel_kassenstuerze WHERE id = ?", (nummer,)
    ).fetchone()
    if row is None:
        return None
    data = {"nummer": nummer, "tag": row[0], **json.loads(row[2]), "summe": row[1]}
    pro_karte = [0] * len(data.get("karten", []))
    for step in data.get("schritte", []):
        if step["typ"] in ("karte", "gewuerz") and step["karte"] < len(pro_karte):
            pro_karte[step["karte"]] += step["plus"]
    data["pro_karte"] = pro_karte
    return data


def chronik(conn, limit: int = 12) -> list[dict]:
    stand.vorbereiten(conn)
    return [
        {"nummer": r[0], "tag": r[1], "summe": r[2]}
        for r in conn.execute(
            "SELECT id, tag, summe FROM spiel_kassenstuerze ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    ]
