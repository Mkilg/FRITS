"""Gewürze: the special cards on Fritte's spice shelf.

Each one rewards a habit that good bookkeeping needs anyway, never a shortcut:
- receipts entered soon after paying (GoBD: zeitgerechte Erfassung, within about 10 days),
- invoices written soon after the work (cash flow; §14 UStG wants them within 6 months),
- following up on unpaid invoices, a steady weekly rhythm, an empty inbox.
Nothing rewards deleting, cancelling or changing anything already booked.

A spice adds Körner on single cards (pro_karte) and/or Würze to the whole Kassensturz
(plus_wuerze adds, mal_wuerze multiplies). Score = Körner × Würze, Würze starts at 1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

GEWICHT = {"gewöhnlich": 6, "selten": 3, "legendär": 1}


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
    return 1.0 if len({k["art"] for k in karten}) >= 3 else 0.0


def _puenktlich(karten: list[dict], umfeld: dict) -> float:
    return 0.5 * sum(1 for k in karten if k["art"] == "bezahlt" and k.get("puenktlich"))


def _stapel(karten: list[dict], umfeld: dict) -> float:
    return float(len(karten) // 5)


def _feierabend(karten: list[dict], umfeld: dict) -> float:
    return 2.0 if any(k["art"] == "feierabend" for k in karten) else 0.0


def _rhythmus(karten: list[dict], umfeld: dict) -> float:
    return 2.0 if umfeld.get("rhythmus_wochen", 0) >= 3 else 1.0


def _aufgeraeumt(karten: list[dict], umfeld: dict) -> float:
    return 1.5 if umfeld.get("aufgeraeumt") else 1.0


def _gereift(karten: list[dict], umfeld: dict) -> float:
    # Grows with every Kassensturz since it came onto the shelf.
    return round(0.2 * umfeld.get("reife", {}).get("malzessig", 0), 1)


GEWUERZE: tuple[Gewuerz, ...] = (
    Gewuerz("meersalz", "Meersalz", "🧂", "gewöhnlich", 5,
            "+2 Körner für jede Rechnung.", pro_karte=_art("rechnung", 2)),
    Gewuerz("muskat", "Muskat", "🌰", "gewöhnlich", 5,
            "+1 Korn für jede Ausgabe.", pro_karte=_art("ausgabe", 1)),
    Gewuerz("schnittlauch", "Schnittlauch", "🌱", "gewöhnlich", 6,
            "+3 Körner für jedes Angebot.", pro_karte=_art("angebot", 3)),
    Gewuerz("paprika", "Paprika", "🫑", "gewöhnlich", 7,
            "+3 Körner für jeden Beleg, den du innerhalb von 10 Tagen nach dem Bezahlen"
            " eingetragen hast.", pro_karte=_art("ausgabe", 3, "zeitnah")),
    Gewuerz("rosmarin", "Rosmarin", "🌿", "gewöhnlich", 7,
            "+4 Körner für jede Rechnung, die höchstens 7 Tage nach der Arbeit geschrieben"
            " wurde.", pro_karte=_art("rechnung", 4, "frisch")),
    Gewuerz("pfeffer", "Pfeffer", "⚫", "gewöhnlich", 8,
            "+1 Würze, wenn mindestens drei verschiedene Kartenarten dabei sind.",
            plus_wuerze=_kartenarten),
    Gewuerz("knoblauch", "Knoblauch", "🧄", "selten", 10,
            "+5 Körner für jede Zahlungserinnerung. Freundlich nachhaken lohnt sich.",
            pro_karte=_art("erinnerung", 5)),
    Gewuerz("zimt", "Zimt", "🪵", "selten", 10,
            "+2 Würze, wenn ein Feierabend dabei ist.", plus_wuerze=_feierabend),
    Gewuerz("curry", "Curry", "🍛", "selten", 12,
            "+0,5 Würze für jede Zahlung, die vor dem Zahlungsziel kam.",
            plus_wuerze=_puenktlich),
    Gewuerz("chili", "Chili", "🌶️", "selten", 12,
            "+1 Würze für je 5 Karten im Kassensturz. Gut für einen Büro-Block.",
            plus_wuerze=_stapel),
    Gewuerz("malzessig", "Malzessig", "🍶", "legendär", 20,
            "Reift im Regal: +0,2 Würze für jeden Kassensturz, seit er da ist.",
            plus_wuerze=_gereift),
    Gewuerz("trueffelsalz", "Trüffelsalz", "🍄", "legendär", 22,
            "Würze ×2, wenn du in jeder der letzten drei Wochen Papierkram gemacht hast.",
            mal_wuerze=_rhythmus),
    Gewuerz("safran", "Safran", "🌸", "legendär", 25,
            "Würze ×1,5, wenn der Eingang leer ist und kein Entwurf auf dich wartet.",
            mal_wuerze=_aufgeraeumt),
)  # fmt: skip
NACH_ID = {g.id: g for g in GEWUERZE}
