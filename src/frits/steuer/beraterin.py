"""Eine Mail der Steuerberaterin in Sammelstücke übersetzen.

Die Mail wird hier ganz ohne KI gelesen, auf diesem Computer: Aufzählungen und Sätze
wie "ich bräuchte noch ..." werden zu Wünschen. Jeder Wunsch landet entweder bei einem
passenden Sammelstück (das bekommt dann das Schildchen "Wunsch von ihr") oder als neues
Sammelstück in der Provinz, zu der die Wörter am besten passen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from frits.steuer.katalog import Gebiet

_AUFZAEHLUNG = re.compile(r"^\s*(?:[-*•–·]|\d{1,2}[.)])\s+(.+)$")
_WUNSCHWORTE = re.compile(
    r"\b(bräuchte|benötige|brauche|fehlt|fehlen|schick|sende|bitte um|noch)\b", re.IGNORECASE
)
_GRUSS = re.compile(
    r"^(liebe|lieber|hallo|hi|guten tag|sehr geehrte|viele grüße|beste grüße|lg|vg|mfg|"
    r"herzliche grüße|danke|viele grüsse)\b",
    re.IGNORECASE,
)
_ANREDE = re.compile(r"^(liebe|lieber|hallo|hi|guten tag|sehr geehrte)\b[^,]*,\s*", re.IGNORECASE)
_ZAHLWORTE = {"eins": 1, "einen": 1, "erste": 1, "ersten": 1, "zwei": 2, "zweite": 2, "zweiten": 2}
_FUELLWORTE = {
    "die",
    "der",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einer",
    "einen",
    "und",
    "oder",
    "für",
    "von",
    "vom",
    "zum",
    "zur",
    "aus",
    "deinem",
    "deiner",
    "deine",
    "dein",
    "ihrem",
    "ihrer",
    "ihre",
    "noch",
    "bitte",
    "auch",
    "mit",
    "über",
    "alle",
    "beide",
    "beiden",
    "jahr",
}


@dataclass(frozen=True)
class Wunsch:
    text: str
    gebiete: tuple[str, ...]  # Gebiet-IDs, z. B. ("gehoeft-1", "gehoeft-2")
    stueck_id: int | None  # passendes Sammelstück, falls es schon eins gibt


def wuensche_aus_text(text: str) -> list[str]:
    zeilen = [z.strip() for z in text.replace("\r", "").split("\n")]
    punkte = [m.group(1).strip() for z in zeilen if (m := _AUFZAEHLUNG.match(z))]
    if punkte:
        return [p.rstrip(".;,") for p in punkte]
    # Keine Aufzählung: Sätze mit Wunschwörtern nehmen
    saetze = [_ANREDE.sub("", s.strip()) for s in re.split(r"(?<=[.!?])\s+|\n", text)]
    return [s.rstrip(".;,") for s in saetze if s and _WUNSCHWORTE.search(s) and not _GRUSS.match(s)]


def _woerter(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if len(w) > 2 and w not in _FUELLWORTE
    }


def _passt(wort: str, stichwort: str) -> bool:
    """ "versicherungsnachweise" passt zu "versicherungen", "dividenden" zu "dividende"."""
    if min(len(wort), len(stichwort)) < 4:
        return wort == stichwort
    gleich = 0
    for a, b in zip(wort, stichwort, strict=False):
        if a != b:
            break
        gleich += 1
    return gleich >= min(len(wort), len(stichwort), 6)


def _punkte(woerter: set[str], gebiet: Gebiet) -> int:
    """Wie viele Wörter des Wunsches zu dieser Provinz passen (jedes Wort zählt einmal)."""
    stichworte = set(gebiet.provinz.woerter) | _woerter(gebiet.provinz.bereich)
    for s in gebiet.provinz.stuecke:
        stichworte |= _woerter(s.name)
    return sum(1 for w in woerter if any(_passt(w, s) for s in stichworte))


def _nummern(text: str) -> set[int]:
    gefunden = {int(n) for n in re.findall(r"(?:wohnung|nr\.?|objekt)\s*(\d+)", text.lower())}
    for wort, zahl in _ZAHLWORTE.items():
        if re.search(rf"\b{wort}\s+wohnung", text.lower()):
            gefunden.add(zahl)
    return gefunden


def zuordnen(text: str, gebiete: list[Gebiet], stuecke: list[dict]) -> list[Wunsch]:
    result = []
    for w in wuensche_aus_text(text):
        woerter = _woerter(w)
        punkte = {g.id: _punkte(woerter, g) for g in gebiete}
        beste = max(punkte.values(), default=0)
        if beste == 0:
            ziel = [next((g.id for g in gebiete if g.provinz.key == "burg"), gebiete[0].id)]
        else:
            ziel = [g for g in gebiete if punkte[g.id] == beste]
            nummern = _nummern(w)
            mehrere = re.search(r"\b(beide|beiden|alle|allen)\b", w.lower())
            if nummern:
                ziel = [g for g in ziel if g.nummer in nummern] or ziel
            elif not mehrere:
                ziel = ziel[:1]
            ziel = [g.id for g in ziel]
        # Gibt es schon ein passendes Sammelstück? Dann kein neues anlegen.
        treffer = None
        for s in stuecke:
            if s["gebiet"] not in ziel:
                continue
            gemeinsam = sum(1 for a in woerter for b in _woerter(s["name"]) if _passt(a, b))
            if gemeinsam and (treffer is None or gemeinsam > treffer[0]):
                treffer = (gemeinsam, s["id"])
        if treffer and len(ziel) == 1:
            result.append(Wunsch(w, tuple(ziel), treffer[1]))
        else:
            result.append(Wunsch(w, tuple(ziel), None))
    return result
