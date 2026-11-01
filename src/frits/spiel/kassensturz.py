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
from frits.spiel import album, ausbau, saison, stand
from frits.spiel.gewuerze import NACH_ID, Gewuerz

ZEITNAH_TAGE = 10  # GoBD: record bookings promptly; here: receipt entered within 10 days of arrival
FRISCH_TAGE = 7  # invoice written within a week of the note coming in
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


def _eingang(conn, draft_id) -> date | None:
    """When the note or email about the work arrived in the inbox. Set by FRITS, never
    typed. An invoice typed by hand has no such moment, so it is never "frisch"."""
    r = stand.rows(
        conn,
        "SELECT j.received FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE d.id = ?",
        (draft_id,),
    )
    return _tag(r[0][0]) if r else None


def _karte(conn, schluessel: str, art: str, gesehen: str, ausgaben=None) -> Karte | None:
    """The card with what the spices need to know, or None if the paperwork is gone.

    The facts the spices look at ("frisch", "zeitnah", "pünktlich") come from dates FRITS
    records by itself, or from the payment the customer made. None of them gets better by
    typing a different date, so the game never makes a wrong date attractive."""
    key = schluessel.split(":", 1)[1]
    base = stand.GRUND.get(art, 0)
    if art == "korrektur":
        echt = _karte(conn, schluessel, schluessel.split(":", 1)[0], gesehen, ausgaben)
        if echt is None:
            return None
        return Karte(
            schluessel, art, f"{echt.titel} (Korrektur)",
            "ersetzt eine stornierte Rechnung, darum ohne Körner", 0, {},
        )  # fmt: skip
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
        came, written = _eingang(conn, draft_id), _tag(issue)
        frisch = bool(came and written and 0 <= (written - came).days <= FRISCH_TAGE)
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
        erinnert = any(
            paid_day and (_tag(c) or paid_day) <= paid_day
            for (c,) in stand.rows(
                conn, "SELECT created FROM reminders WHERE invoice_number = ?", (key,)
            )
        )
        return Karte(
            schluessel, art, "Zahlung eingegangen", f"{key} · {customer} · {_euro(gross)}", base,
            {"puenktlich": puenktlich, "nach_erinnerung": erinnert},
        )  # fmt: skip
    if art == "angebot":
        r = stand.rows(
            conn,
            "SELECT created FROM quotes WHERE number = ?"
            " AND (status = 'accepted' OR draft_id IS NOT NULL)",
            (key,),
        )
        if not r:
            return None
        return Karte(schluessel, art, f"Angebot {key}", "angenommen", base, {})
    if art == "erinnerung":
        if not key.isdigit():
            return None
        r = stand.rows(
            conn, "SELECT invoice_number, level FROM reminders WHERE id = ?", (int(key),)
        )
        if not r or _storniert(conn, r[0][0]):
            return None
        return Karte(
            schluessel, art, "Erinnerung", f"Rechnung {r[0][0]} · Stufe {r[0][1]}", base, {}
        )
    if art == "ausgabe":
        rid = (ausgaben if ausgaben is not None else stand.ausgaben(conn)).get(schluessel)
        if rid is None:
            return None
        r = stand.rows(
            conn,
            "SELECT e.vendor, e.gross, (SELECT MIN(j.received) FROM jobs j"
            " WHERE j.filename = e.source_file) FROM expenses e"
            " WHERE e.id = ? AND e.struck IS NULL",
            (rid,),
        )
        if not r:
            return None
        vendor, gross, came = r[0]
        came_day, seen = _tag(came), _tag(gesehen)
        zeitnah = bool(came_day and seen and 0 <= (seen - came_day).days <= ZEITNAH_TAGE)
        return Karte(
            schluessel, art, "Ausgabe", f"{vendor} · {_euro(gross)}", base, {"zeitnah": zeitnah}
        )
    if art == "feierabend":
        day = _tag(key)
        return Karte(
            schluessel, art, "Feierabend", day.strftime("%d.%m.%Y") if day else key, base, {}
        )
    if art == "steuer":
        r = stand.rows(
            conn,
            "SELECT name, jahr FROM steuer_stuecke WHERE id = ? AND status = 'gesammelt'",
            (key,),
        )
        if not r:
            return None
        return Karte(schluessel, art, "Steuer-Unterlage", f"{r[0][0]} · {r[0][1]}", base, {})
    if art == "steuerjahr":
        r = stand.rows(conn, "SELECT 1 FROM steuer_chronik WHERE jahr = ? AND art = 'sieg'", (key,))
        if not r:
            return None
        return Karte(schluessel, art, f"Steuerjahr {key}", "Der Drache ist besiegt!", base, {})
    if art == "start":
        return Karte(
            schluessel, art, "Fritte zieht ein", "Willkommen! Dazu +1 Würze", base,
            {"plus_wuerze": 1.0},
        )  # fmt: skip
    return None


def offene_karten(conn: sqlite3.Connection) -> list[Karte]:
    """Waiting cards, oldest first. Cards whose paperwork was deleted or cancelled
    drop out for good: undoing work never earns anything."""
    stand.vorbereiten(conn)
    karten, weg = [], []
    ausgaben = stand.ausgaben(conn)
    for schluessel, art, gesehen in conn.execute(
        "SELECT schluessel, art, gesehen FROM spiel_karten"
        " WHERE kassensturz IS NULL AND art != 'pflege' ORDER BY gesehen, rowid"
    ).fetchall():
        karte = _karte(conn, schluessel, art, gesehen, ausgaben)
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
    return GRUND_PLAETZE + ausbau.extra_plaetze(conn)


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
    for i, karte in enumerate(karten):  # e.g. Fritte's move-in card brings Würze itself
        if karte.get("plus_wuerze"):
            wuerze = round(wuerze + karte["plus_wuerze"], 2)
            schritte.append(
                {"typ": "plus", "gewuerz": "", "karte": i, "plus": karte["plus_wuerze"],
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
    for k in karten:  # the season's theme (of the day the card came): one kind brings double
        if k.art == saison.THEMEN[saison.quartal(_tag(gesehen_von(conn, k)) or heute)[0]][0]:
            k.koerner *= 2
            k.fakten["saison"] = True
    return karten, berechnen(
        [k.als_dict() for k in karten], regal(conn), umfeld(conn, heute, aufgeraeumt)
    )


def gesehen_von(conn, karte: Karte) -> str:
    r = conn.execute(
        "SELECT gesehen FROM spiel_karten WHERE schluessel = ?", (karte.schluessel,)
    ).fetchone()
    return r[0] if r else ""


def umfeld(conn, heute: date, aufgeraeumt: bool) -> dict:
    """What the spices may look at besides the cards."""
    return {
        "rhythmus_wochen": rhythmus_wochen(conn, heute),
        "aufgeraeumt": aufgeraeumt,
        "reife": reife(conn),
        "grundwuerze": ausbau.grundwuerze(conn),
    }


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
        if any(k.art == "start" for k in karten):  # the welcome round is a present
            stand.setze(conn, "willkommen_ks", str(nummer))
        stand.buche(
            conn, heute.isoformat(), ergebnis["summe"], "kassensturz",
            f"Kassensturz Nr. {nummer}: {n} {'Karte' if n == 1 else 'Karten'}",
        )  # fmt: skip
        woche = stand.woche(heute)
        for g in shelf:  # spices mature once a week with a Kassensturz, not per round
            if stand.einstellung(conn, f"reifewoche:{g.id}") != woche:
                stand.setze(conn, f"reifewoche:{g.id}", woche)
                key = f"reife:{g.id}"
                stand.setze(conn, key, str(int(stand.einstellung(conn, key, "0")) + 1))
        fertig = ausbau.fertigstellen(conn)
        if fertig:
            record["ausbau"] = {
                "name": fertig.name, "zeichen": fertig.zeichen,
                "text": ausbau.stufen_text(conn, fertig),
            }  # fmt: skip
        geschafft = saison.pruefen(conn, heute)
        if geschafft:
            record["saison"] = {
                "name": geschafft.name, "ziel": geschafft.ziel, "erreicht": geschafft.erreicht,
                "bonus": geschafft.ziel // saison.BONUS_ANTEIL,
            }  # fmt: skip
        neu = album.neue(conn, heute)
        if fertig:  # the house banner already says it; the sticker is in the album anyway
            neu = [e for e in neu if e.id not in album.AUSBAU_STICKER]
        if neu:
            record["erfolge"] = [
                {"name": e.name, "zeichen": e.zeichen, "text": e.text} for e in neu
            ]
        if fertig or neu or geschafft:
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
    data["pro_karte"] = pro_karte(data, len(data.get("karten", [])))
    return data


def pro_karte(ergebnis: dict, n: int) -> list[int]:
    """Körner per card, spices included (what the card shows in the show)."""
    summen = [0] * n
    for step in ergebnis.get("schritte", []):
        if step["typ"] in ("karte", "gewuerz") and step["karte"] < n:
            summen[step["karte"]] += step["plus"]
    return summen


def chronik(conn, limit: int = 12) -> list[dict]:
    stand.vorbereiten(conn)
    return [
        {"nummer": r[0], "tag": r[1], "summe": r[2]}
        for r in conn.execute(
            "SELECT id, tag, summe FROM spiel_kassenstuerze ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    ]
