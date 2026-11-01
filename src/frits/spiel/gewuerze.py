"""Gewürze: the special cards on Fritte's spice shelf.

Each one rewards a habit that good bookkeeping needs anyway, never a shortcut:
- receipts entered soon after they came in (GoBD: zeitgerechte Erfassung),
- invoices written soon after the note about the work came in (cash flow; §14 UStG),
- a payment that came after a friendly reminder, a steady weekly rhythm, an empty inbox.
Nothing rewards deleting, cancelling or changing anything already booked, and no bonus
depends on a date Fritz types in, so a wrong date never pays off.

A spice adds Körner on single cards (pro_karte) and/or Würze to the whole Kassensturz
(plus_wuerze adds, mal_wuerze multiplies). Score = Körner × Würze, Würze starts at 1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

GEWICHT = {"gewöhnlich": 6, "selten": 3, "legendär": 1}
MALZESSIG_WOCHEN = 10  # Malzessig is fully matured after ten weeks
# Würze that grows with the number of cards stops here: otherwise one big Kassensturz a
# quarter would bring far more than one a week, and the weekly rhythm is the point.
CHILI_HOECHSTENS = 2.0
CURRY_HOECHSTENS = 2.0
# Cards that are no paperwork of their own: they don't make a pile or a mix bigger.
NUR_KLICKS = frozenset({"korrektur", "aufgabe", "lernen", "joker"})  # joker: from a chest


@dataclass(frozen=True)
class Gewuerz:
    id: str
    name: str
    zeichen: str
    seltenheit: str  # gewöhnlich | selten | legendär
    preis: int
    text: str
    pro_karte: Callable[[dict, dict], int] | None = None  # (karte, umfeld) -> extra Körner
    plus_wuerze: Callable[[list[dict], dict], float] | None = None
    mal_wuerze: Callable[[list[dict], dict], float] | None = None  # 1 means no change


def _art(art: str, koerner: int, bedingung: str | None = None):
    def wirkt(karte: dict, umfeld: dict) -> int:
        if karte["art"] != art:
            return 0
        if bedingung and not karte.get(bedingung):
            return 0
        return koerner

    return wirkt


def _kartenarten(karten: list[dict], umfeld: dict) -> float:
    return 1.0 if len({k["art"] for k in karten} - NUR_KLICKS) >= 3 else 0.0


def _zahlungen(karten: list[dict], umfeld: dict) -> float:
    # Not "on time": the payment date is the day Fritz clicks "Bezahlt", so rewarding an
    # early date would reward clicking before the money is there (EÜR: Zuflussprinzip).
    return min(CURRY_HOECHSTENS, 0.5 * sum(1 for k in karten if k["art"] == "bezahlt"))


def _stapel(karten: list[dict], umfeld: dict) -> float:
    zaehlen = [k for k in karten if k["art"] not in NUR_KLICKS]
    return min(CHILI_HOECHSTENS, float(len(zaehlen) // 5))


def _feierabend(karten: list[dict], umfeld: dict) -> float:
    return 2.0 if any(k["art"] == "feierabend" for k in karten) else 0.0


def _rhythmus(karten: list[dict], umfeld: dict) -> float:
    return 2.0 if umfeld.get("rhythmus_wochen", 0) >= 3 else 1.0


def _aufgeraeumt(karten: list[dict], umfeld: dict) -> float:
    return 1.5 if umfeld.get("aufgeraeumt") else 1.0


PAPIERKRAM = frozenset({"rechnung", "bezahlt", "ausgabe", "angebot", "erinnerung"})
SALBEI_AB = 3  # real paperwork cards it takes for Salbei


def _papierkram(karten: list[dict]) -> int:
    return sum(1 for k in karten if k["art"] in PAPIERKRAM)


def _salbei(karten: list[dict], umfeld: dict) -> float:
    return 1.5 if _papierkram(karten) >= SALBEI_AB else 1.0


def _gereift(karten: list[dict], umfeld: dict) -> float:
    # Grows with every week that had a Kassensturz since it came onto the shelf, up to +2.
    return round(0.2 * min(MALZESSIG_WOCHEN, umfeld.get("reife", {}).get("malzessig", 0)), 1)


GEWUERZE: tuple[Gewuerz, ...] = (
    Gewuerz("meersalz", "Meersalz", "🌊", "gewöhnlich", 5,
            "+2 Körner für jede Rechnung oder Gutschrift.", pro_karte=_art("rechnung", 2)),
    Gewuerz("muskat", "Muskat", "🌰", "gewöhnlich", 5,
            "+1 Korn für jede Ausgabe.", pro_karte=_art("ausgabe", 1)),
    Gewuerz("schnittlauch", "Schnittlauch", "🌱", "gewöhnlich", 6,
            "+3 Körner für jedes Angebot, aus dem eine Rechnung wurde.", pro_karte=_art("angebot", 3)),
    Gewuerz("paprika", "Paprika", "🫑", "gewöhnlich", 7,
            "+3 Körner für jeden Beleg, den du innerhalb von 10 Tagen nach dem Eingang in"
            " FRITS eingetragen hast.", pro_karte=_art("ausgabe", 3, "zeitnah")),
    Gewuerz("rosmarin", "Rosmarin", "🌿", "gewöhnlich", 7,
            "+4 Körner für jede Rechnung, die höchstens 7 Tage nach dem Eingang der Notiz"
            " fertig war.", pro_karte=_art("rechnung", 4, "frisch")),
    Gewuerz("salbei", "Salbei", "🍃", "gewöhnlich", 9,
            "Würze ×1,5, wenn mindestens drei Karten echter Papierkram sind (Rechnung,"
            " Zahlung, Ausgabe, Angebot oder Erinnerung).", mal_wuerze=_salbei),
    Gewuerz("pfeffer", "Pfeffer", "⚫", "gewöhnlich", 8,
            "+1 Würze, wenn mindestens drei verschiedene Kartenarten dabei sind.",
            plus_wuerze=_kartenarten),
    Gewuerz("knoblauch", "Knoblauch", "🧄", "selten", 10,
            "+4 Körner für jede Zahlung, die nach einer freundlichen Erinnerung kam.",
            pro_karte=_art("bezahlt", 4, "nach_erinnerung")),
    Gewuerz("zimt", "Zimt", "🪵", "selten", 10,
            "+2 Würze, wenn ein Feierabend dabei ist.", plus_wuerze=_feierabend),
    Gewuerz("curry", "Curry", "🍛", "selten", 12,
            "+0,5 Würze für jede eingegangene Zahlung (bis +2).", plus_wuerze=_zahlungen),
    Gewuerz("chili", "Chili", "🌶️", "selten", 12,
            "+1 Würze für je 5 Karten im Kassensturz (bis +2). Gut für einen Büro-Block.",
            plus_wuerze=_stapel),
    Gewuerz("malzessig", "Malzessig", "🍶", "legendär", 20,
            "Reift im Regal: +0,2 Würze für jede Woche mit Kassensturz, seit er da ist"
            " (bis +2).",
            plus_wuerze=_gereift),
    Gewuerz("trueffelsalz", "Trüffelsalz", "🍄", "legendär", 22,
            "Würze ×2, wenn du in jeder der letzten drei Wochen Papierkram gemacht hast.",
            mal_wuerze=_rhythmus),
    Gewuerz("safran", "Safran", "🌸", "legendär", 25,
            "Würze ×1,5, wenn der Eingang leer ist und kein Entwurf auf dich wartet"
            " (auch keiner, den du in den letzten 7 Tagen verworfen hast).",
            mal_wuerze=_aufgeraeumt),
)  # fmt: skip
NACH_ID = {g.id: g for g in GEWUERZE}


# ---------- what each spice does with the cards on the table ----------
# A spice that works says what it brings; one that doesn't says what is still missing.
# Nothing is hidden: a jar that stays quiet must not look like a bug.


def _fehlt_karte(was: str):
    return lambda karten, umfeld: f"wartet auf {was}"


def _fehlt_arten(karten: list[dict], umfeld: dict) -> str:
    n = len({k["art"] for k in karten} - NUR_KLICKS)
    return f"{n} von 3 Kartenarten (Abgehakt und Gelernt zählen nicht)"


def _fehlt_stapel(karten: list[dict], umfeld: dict) -> str:
    n = sum(1 for k in karten if k["art"] not in NUR_KLICKS)
    return f"{n} von 5 Karten"


FEHLT: dict[str, Callable[[list[dict], dict], str]] = {
    "meersalz": _fehlt_karte("eine Rechnung oder Gutschrift"),
    "muskat": _fehlt_karte("eine Ausgabe"),
    "schnittlauch": _fehlt_karte("ein Angebot, aus dem eine Rechnung wurde"),
    "paprika": _fehlt_karte("einen Beleg, der bis 10 Tage nach dem Eingang eingetragen war"),
    "rosmarin": _fehlt_karte("eine Rechnung, die bis 7 Tage nach der Notiz fertig war"),
    "knoblauch": _fehlt_karte("eine Zahlung nach einer freundlichen Erinnerung"),
    "pfeffer": _fehlt_arten,
    "zimt": _fehlt_karte("einen Feierabend"),
    "curry": _fehlt_karte("eine eingegangene Zahlung"),
    "chili": _fehlt_stapel,
    "malzessig": lambda karten, umfeld: "reift ab der ersten Woche im Regal",
    "trueffelsalz": lambda karten, umfeld: (
        f"{umfeld.get('rhythmus_wochen', 0)} von 3 Wochen mit Papierkram"
    ),
    "safran": lambda karten, umfeld: "Eingang oder ein Entwurf wartet noch",
    "salbei": lambda karten, umfeld: (
        f"{_papierkram(karten)} von {SALBEI_AB} Karten echter Papierkram"
    ),
}


def _zahl(w: float) -> str:
    return f"{w:g}".replace(".", ",")


def wirkung(g: Gewuerz, karten: list[dict], umfeld: dict) -> tuple[bool, str]:
    """(works, short text) for one spice with these cards, e.g. (True, "+4 Körner")
    or (False, "2 von 3 Kartenarten")."""
    teile = []
    if g.pro_karte:
        extra = sum(g.pro_karte(k, umfeld) for k in karten)
        if extra:
            teile.append(f"+{extra} {'Korn' if extra == 1 else 'Körner'}")
    if g.plus_wuerze:
        plus = g.plus_wuerze(karten, umfeld)
        if plus:
            teile.append(f"+{_zahl(plus)} Würze")
    if g.mal_wuerze:
        mal = g.mal_wuerze(karten, umfeld)
        if mal != 1:
            teile.append(f"Würze ×{_zahl(mal)}")
    if teile:
        return True, " und ".join(teile)
    fehlt = FEHLT.get(g.id)
    return False, fehlt(karten, umfeld) if fehlt else "greift diesmal nicht"


# ---------- Tagesgericht: one choice before each Kassensturz ----------
# Fritte cooks one dish for the Kassensturz. Two change every day; the third is always
# "Einkochen": nothing today, +1 Würze next time. Which one is best depends on the cards on
# the table, so it is a real (small) decision. Never a way to lose anything.

EINKOCHEN = "einkochen"


def _bunt(karten: list[dict], umfeld: dict) -> float:
    return 0.5 * len({k["art"] for k in karten} - NUR_KLICKS - {"start"})


def _eingekocht(karten: list[dict], umfeld: dict) -> float:
    return 1.0


REZEPTE: tuple[Gewuerz, ...] = (
    Gewuerz("belegsuppe", "Belegsuppe", "🍲", "rezept", 0,
            "+2 Körner für jede Ausgabe.", pro_karte=_art("ausgabe", 2)),
    Gewuerz("rechnungsbraten", "Rechnungsbraten", "🍖", "rezept", 0,
            "+3 Körner für jede Rechnung oder Gutschrift.", pro_karte=_art("rechnung", 3)),
    Gewuerz("zahltagspfanne", "Zahltagspfanne", "🍳", "rezept", 0,
            "+3 Körner für jede eingegangene Zahlung.", pro_karte=_art("bezahlt", 3)),
    Gewuerz("angebotsauflauf", "Angebotsauflauf", "🥧", "rezept", 0,
            "+4 Körner für jedes Angebot, aus dem eine Rechnung wurde.",
            pro_karte=_art("angebot", 4)),
    Gewuerz("steuereintopf", "Steuereintopf", "🥘", "rezept", 0,
            "+2 Körner für jede gesammelte Steuer-Unterlage.", pro_karte=_art("steuer", 2)),
    Gewuerz("bunter_teller", "Bunter Teller", "🥗", "rezept", 0,
            "+0,5 Würze für jede Art Papierkram auf dem Tisch.", plus_wuerze=_bunt),
)  # fmt: skip
EINGEKOCHT = Gewuerz(
    EINKOCHEN, "Eingekochtes", "🫙", "rezept", 0,
    "Heute nichts extra. Dafür +1 Würze beim nächsten Kassensturz.",
    plus_wuerze=_eingekocht,
)  # fmt: skip
REZEPT_NACH_ID = {g.id: g for g in (*REZEPTE, EINGEKOCHT)}
