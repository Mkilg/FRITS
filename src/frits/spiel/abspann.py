"""The day's credits for the Feierabend page: what the game made of today's real work.

A tester (playing Marie) said this is what she would show Fritz: a calm closing list, not
a new screen to play on. Only what happened today, in a few short lines, nothing to click.
Read only; the Steuer-Quest's tables are optional.
"""

from __future__ import annotations

import json
from datetime import date

from frits.spiel import album, stand, tagesziel


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
