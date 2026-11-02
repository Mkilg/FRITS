"""The day's credits for the Feierabend page: what the game made of today's real work.

A tester (playing Marie) said this is what she would show Fritz: a calm closing list, not
a new screen to play on. Only what happened today, in a few short lines, nothing to click.
Read only; the Steuer-Quest's tables are optional.
"""

from __future__ import annotations

import json
from datetime import date

from frits.spiel import album, stand, tagesziel

# Salt the Steuer-Quest pays into Fritte's purse (stand.beutel_gutschreiben: art "quest",
# take-backs negative; "drachensieg" from before the one purse).
QUEST_SALZ = "SELECT SUM(betrag) FROM spiel_kasse WHERE art IN ('quest', 'drachensieg') AND tag = ?"


def _zahl(conn, sql: str, args: tuple) -> int:
    r = stand.rows(conn, sql, args)
    return int(r[0][0] or 0) if r else 0


def heute(conn, tag: date, mit_fritte: bool = True) -> list[str]:
    """Short German lines, in the order of the day's story. Empty when nothing happened."""
    stand.vorbereiten(conn)
    t = tag.isoformat()
    zeilen = []
    z = tagesziel.zaehlen(conn, tag)
    if z["erledigt"]:
        zeilen.append(
            "Tagesziel geschafft"
            if z["geschafft"]
            else f"Tagesziel: {z['erledigt']} von {z['ziel']}"
        )
    abgehakt = _zahl(conn, "SELECT COUNT(*) FROM aufgaben WHERE substr(erledigt, 1, 10) = ?", (t,))
    if abgehakt:
        zeilen.append(
            f"{abgehakt} {'Punkt' if abgehakt == 1 else 'Punkte'} auf deiner Liste abgehakt"
        )
    karten = dict(
        stand.rows(
            conn,
            "SELECT art, COUNT(*) FROM spiel_karten WHERE gesehen = ? GROUP BY art",
            (t,),
        )
    )
    for art, eins, mehr in (
        ("ausgabe", "Beleg gebucht", "Belege gebucht"),
        ("bezahlt", "Zahlung eingetragen", "Zahlungen eingetragen"),
        ("lernen", "Kurs-Etappe gelernt", "Kurs-Etappen gelernt"),
    ):
        if n := int(karten.get(art, 0)):
            zeilen.append(f"{n} {eins if n == 1 else mehr}")
    funde = _zahl(
        conn,
        "SELECT COUNT(*) FROM steuer_stuecke WHERE status = 'gesammelt' AND substr(geaendert, 1, 10) = ?",
        (t,),
    )
    if funde:
        zeilen.append(
            f"{funde} {'Unterlage' if funde == 1 else 'Unterlagen'} für die Steuer gesammelt"
        )
    for (name,) in stand.rows(
        conn,
        "SELECT DISTINCT text FROM steuer_chronik WHERE art = 'erfolg' AND substr(zeit, 1, 10) = ?",
        (t,),
    ):
        zeilen.append(f"Orden „{name}“ verdient")
    for (jahr,) in stand.rows(
        conn,
        "SELECT DISTINCT jahr FROM steuer_chronik WHERE art = 'sieg' AND substr(zeit, 1, 10) = ?",
        (t,),
    ):
        zeilen.append(f"Der Papierdrache {jahr} ist besiegt!")
    if not mit_fritte:
        return zeilen
    stuerze = stand.rows(conn, "SELECT summe FROM spiel_kassenstuerze WHERE tag = ?", (t,))
    if stuerze:
        summe = sum(int(s[0]) for s in stuerze)
        zeilen.append(
            f"{'Kassensturz' if len(stuerze) == 1 else f'{len(stuerze)} Kassenstürze'}: "
            f"{summe} Salzkörner für Fritte"
        )
    if (quest := _zahl(conn, QUEST_SALZ, (t,))) > 0:
        zeilen.append(f"Aus der Steuer-Quest: {quest} {'Salzkorn' if quest == 1 else 'Salzkörner'}")
    sachen, koerner = [], 0
    for (inhalt,) in stand.rows(
        conn,
        "SELECT inhalt FROM spiel_truhen WHERE geoeffnet = ? AND inhalt IS NOT NULL ORDER BY reihe",
        (t,),
    ):
        fund = json.loads(inhalt)
        if fund.get("art") == "koerner":
            koerner += int(fund.get("wert", 0))
        elif fund.get("art") in ("ding", "gewuerz"):
            sachen.append(fund.get("name", ""))
    if koerner:
        sachen.append(f"{koerner} Salzkörner")
    if sachen:
        liste = sachen[0] if len(sachen) == 1 else f"{', '.join(sachen[:-1])} und {sachen[-1]}"
        zeilen.append(f"Aus Frittes Truhen: {liste}")
    for e in album.ERFOLGE:
        if stand.einstellung(conn, f"erfolg:{e.id}")[:10] == t:
            zeilen.append(f"Neuer Sticker: {e.name}")
    return zeilen


def _anzahl(n: int, eins: str, mehr: str) -> tuple[int, str]:
    return (n, eins if n == 1 else mehr)


def bilanz(conn, tag: date, mit_fritte: bool = True) -> dict:
    """The closing credits in three parts: one headline (the day's biggest moment), up to
    three tiles (Arbeit, Steuer, Fritte) with their numbers, and the full list below.
    {"schlagzeile": str, "zeichen": str (for the stamp), "kacheln": [{"titel", "zahlen": [(n, label)]}], "zeilen": [...]}."""
    zeilen = heute(conn, tag, mit_fritte)
    if not zeilen:
        return {"schlagzeile": "", "zeichen": "", "kacheln": [], "zeilen": []}
    t = tag.isoformat()
    karten = dict(
        stand.rows(
            conn, "SELECT art, COUNT(*) FROM spiel_karten WHERE gesehen = ? GROUP BY art", (t,)
        )
    )
    abgehakt = _zahl(conn, "SELECT COUNT(*) FROM aufgaben WHERE substr(erledigt, 1, 10) = ?", (t,))
    funde = _zahl(
        conn,
        "SELECT COUNT(*) FROM steuer_stuecke WHERE status = 'gesammelt' AND substr(geaendert, 1, 10) = ?",
        (t,),
    )
    orden = _zahl(
        conn,
        "SELECT COUNT(DISTINCT text) FROM steuer_chronik WHERE art = 'erfolg' AND substr(zeit, 1, 10) = ?",
        (t,),
    )
    siege = [
        r[0]
        for r in stand.rows(
            conn,
            "SELECT DISTINCT jahr FROM steuer_chronik WHERE art = 'sieg' AND substr(zeit, 1, 10) = ?",
            (t,),
        )
    ]
    salz = (
        _zahl(conn, "SELECT SUM(summe) FROM spiel_kassenstuerze WHERE tag = ?", (t,))
        + max(0, _zahl(conn, QUEST_SALZ, (t,)))
        if mit_fritte
        else 0
    )
    truhen = (
        _zahl(conn, "SELECT COUNT(*) FROM spiel_truhen WHERE geoeffnet = ?", (t,))
        if mit_fritte
        else 0
    )
    sticker = (
        sum(1 for e in album.ERFOLGE if stand.einstellung(conn, f"erfolg:{e.id}")[:10] == t)
        if mit_fritte
        else 0
    )
    arbeit = [
        _anzahl(int(karten.get("rechnung", 0)), "Rechnung", "Rechnungen"),
        _anzahl(int(karten.get("ausgabe", 0)), "Beleg", "Belege"),
        _anzahl(int(karten.get("bezahlt", 0)), "Zahlung", "Zahlungen"),
        _anzahl(abgehakt, "Punkt abgehakt", "Punkte abgehakt"),
    ]
    steuer = [_anzahl(funde, "Unterlage", "Unterlagen"), _anzahl(orden, "Orden", "Orden")]
    fritte = [
        _anzahl(salz, "Salzkorn", "Salzkörner"),
        _anzahl(truhen, "Truhe geöffnet", "Truhen geöffnet"),
        _anzahl(sticker, "Sticker", "Sticker"),
    ]
    kacheln = [
        {"titel": titel, "zahlen": [z for z in zahlen if z[0]][:3]}
        for titel, zahlen in (("Arbeit", arbeit), ("Steuer", steuer), ("Fritte", fritte))
        if any(z[0] for z in zahlen)
    ]
    z = tagesziel.zaehlen(conn, tag)
    if siege:
        schlagzeile, zeichen = f"Der Papierdrache {siege[0]} ist besiegt!", "🐉"
    elif orden:
        schlagzeile = "Ein neuer Orden!" if orden == 1 else f"{orden} neue Orden!"
        zeichen = "🏅"
    elif z["geschafft"]:
        schlagzeile, zeichen = "Tagesziel geschafft!", "✓"
    elif salz:
        schlagzeile, zeichen = f"{salz} Salzkörner für Fritte", "🧂"
    else:
        schlagzeile, zeichen = "Ein Stück geschafft.", "✓"
    return {"schlagzeile": schlagzeile, "zeichen": zeichen, "kacheln": kacheln, "zeilen": zeilen}
