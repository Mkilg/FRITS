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
    stuecke: tuple[int | None, ...] = ()  # je Gebiet: vorhandene Karte, oder None (neu anlegen)

    @property
    def stueck_id(self) -> int | None:
        """Die vorhandene Karte, wenn der Wunsch genau ein Gebiet trifft (sonst None)."""
        return self.stuecke[0] if len(self.stuecke) == 1 else None


_TRENNER = re.compile(r"\s*(?:,|;|\bsowie\b|\bund\b|&)\s*", re.IGNORECASE)
# Ohne Doppelpunkt: auch "inkl." und "samt" trennen zwei Unterlagen
_TRENNER_ZEILE = re.compile(
    r"\s*(?:,|;|\bsowie\b|\bund\b|\binkl\.?|\binklusive\b|\bsamt\b|\bplus\b|&)\s*",
    re.IGNORECASE,
)


# Der Anfang einer Sammelzeile, der für alle Teile gilt: "Belege zu", "Nachweise für"
_KOPF = re.compile(r"^(\S+\s+(?:zu|zum|zur|für|über|von|vom))\s+\S", re.IGNORECASE)


def wuensche_aus_text(text: str) -> list[str]:
    zeilen = [z.strip() for z in text.replace("\r", "").split("\n")]
    punkte = [m.group(1).strip() for z in zeilen if (m := _AUFZAEHLUNG.match(z))]
    if punkte:
        return [p.rstrip(".;,") for p in punkte]
    # Keine Aufzählung: Sätze mit Wunschwörtern nehmen
    saetze = [_ANREDE.sub("", s.strip()) for s in re.split(r"(?<=[.!?])\s+|\n", text)]
    return [s.rstrip(".;,") for s in saetze if s and _WUNSCHWORTE.search(s) and not _GRUSS.match(s)]


def zerlegen(zeile: str) -> list[tuple[str, str]]:
    """Eine Sammelzeile in einzelne Wünsche: (Wunsch, Zusammenhang).

    "Für beide Wohnungen: Hausgeld, Grundsteuer, Zinsbescheinigung" wird zu drei Wünschen,
    die alle den Zusammenhang "Für beide Wohnungen" behalten (der sagt, wohin sie gehören).
    Ohne Doppelpunkt wird nur an Kommas geteilt, wenn jedes Stück ein eigenes Ding ist
    (mindestens ein Wort mit 5 Buchstaben): "Kontoauszüge Januar bis März" bleibt ganz."""
    vorne, doppelpunkt, hinten = zeile.partition(":")
    if doppelpunkt and hinten.strip():
        teile = [t.strip() for t in _TRENNER.split(hinten) if t and t.strip()]
        if len(teile) > 1:
            return [(t, vorne.strip()) for t in teile]
        return [(hinten.strip(), vorne.strip())]
    teile = [t.strip() for t in _TRENNER_ZEILE.split(zeile) if t and t.strip()]
    if len(teile) > 1 and all(
        any(len(w) >= 5 for w in re.findall(r"[a-zäöüß]+", t.lower()))
        and len(t.split()) <= 5
        and not t.endswith("-")  # "Kranken- und Pflegeversicherung" bleibt ganz
        for t in teile
    ):
        # "Belege zu Arbeitszimmer und Reisekosten": "Belege zu" gilt für beide Teile
        kopf = _KOPF.match(teile[0])
        zusammenhang = kopf.group(1) if kopf else ""
        return [(teile[0], "")] + [(t, zusammenhang) for t in teile[1:]]
    return [(zeile, "")]


def _woerter(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if len(w) > 2 and w not in _FUELLWORTE
    }


def _passt(wort: str, stichwort: str) -> bool:
    """ "versicherungsnachweise" passt zu "versicherungen", "dividenden" zu "dividende"."""
    if min(len(wort), len(stichwort)) < 4:
        return wort == stichwort
    # Zusammengesetzte Wörter: "vertrag" passt zu "gesellschaftsvertrag"
    if min(len(wort), len(stichwort)) >= 6 and (
        wort.endswith(stichwort) or stichwort.endswith(wort)
    ):
        return True
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
    return sum(1 for w in woerter if any(_passt_provinz(w, s) for s in stichworte))


def _passt_provinz(wort: str, stichwort: str) -> bool:
    """Wie _passt, aber allgemeine Wörter zählen nur genau: "Reisekosten" gehört nicht zur
    Gilde, nur weil dort eine Karte "Eigene Kosten" heißt."""
    if min(wort, stichwort, key=len) in _ALLGEMEIN:
        return wort == stichwort
    return _passt(wort, stichwort)


def _nummern(text: str) -> set[int]:
    gefunden = {int(n) for n in re.findall(r"(?:wohnung|nr\.?|objekt)\s*(\d+)", text.lower())}
    for wort, zahl in _ZAHLWORTE.items():
        if re.search(rf"\b{wort}\s+wohnung", text.lower()):
            gefunden.add(zahl)
    return gefunden


# Wörter, die in vielen Karten stecken: Sie zählen nur, wenn sie genau so dastehen.
# Sonst landet "Steuerbescheinigung" bei der "Steuer-ID".
_ALLGEMEIN = {
    "steuer",
    "steuern",
    "bescheid",
    "bescheinigung",
    "nachweis",
    "jahres",
    "liste",
    "konto",
    "abrechnung",
    "unterlagen",
    "übersicht",
    "kosten",
    "beitrag",
    "beiträge",
}


def _staerke(wort: str, stichwort: str) -> int:
    """3: dasselbe Wort oder sein Anfang ("hausgeld" in "hausgeldabrechnung").
    2: das Ende eines zusammengesetzten Worts ("quittungen" in "spendenquittungen").
    1: nur ähnlicher Anfang ("jahresbescheinigung", "jahresübersicht"). 0: passt nicht."""
    if wort == stichwort:
        return 3
    kurz = min(wort, stichwort, key=len)
    if kurz in _ALLGEMEIN:
        return 0
    if len(kurz) >= 4 and (wort.startswith(stichwort) or stichwort.startswith(wort)):
        return 3
    if len(kurz) >= 6 and (wort.endswith(stichwort) or stichwort.endswith(wort)):
        return 2
    return 1 if _passt(wort, stichwort) else 0


def _karten_punkte(woerter: set[str], stueck: dict) -> int:
    """Wie gut ein Wunsch zu einer vorhandenen Karte passt. Ein Wort im Namen zählt am
    meisten, eins im Hinweis auch ("KSK" steht im Hinweis der Künstlersozialkasse-Karte),
    ein nur ähnlicher Wortanfang wenig."""
    name = _woerter(stueck["name"])
    hinweis = (
        set() if stueck.get("herkunft") == "beraterin" else _woerter(stueck.get("hinweis", ""))
    )
    punkte = 0
    for w in woerter:
        im_namen = max((_staerke(w, n) for n in name), default=0)
        im_hinweis = max((_staerke(w, h) for h in hinweis), default=0)
        punkte += max(im_namen, im_hinweis - 1)
    return punkte


def _provinz(gebiet_id: str) -> str:
    return gebiet_id.split("-")[0]


def zuordnen(text: str, gebiete: list[Gebiet], stuecke: list[dict]) -> list[Wunsch]:
    """Jede Zeile der Mail wird zu einem oder mehreren Wünschen.

    Zuerst sucht FRITS eine Karte, die es schon gibt (in allen Provinzen): Die wird nur
    markiert, statt eine Doppelkarte anzulegen. Nur wenn nichts passt, entsteht eine neue
    Karte in der Provinz, zu der die Wörter am besten passen."""
    ergebnis: list[Wunsch] = []
    gesehen: set[tuple[str, ...]] = set()
    for zeile in wuensche_aus_text(text):
        for teil, zusammenhang in zerlegen(zeile):
            wunsch = _ein_wunsch(teil, zusammenhang, gebiete, stuecke)
            schluessel = tuple(f"{g}:{s}" for g, s in zip(wunsch.gebiete, wunsch.stuecke))
            if any(wunsch.stuecke) and schluessel in gesehen:
                continue  # zweimal dieselbe Karte in einer Mail: einmal reicht
            gesehen.add(schluessel)
            ergebnis.append(wunsch)
    return ergebnis


def _ein_wunsch(teil: str, zusammenhang: str, gebiete: list[Gebiet], stuecke: list[dict]) -> Wunsch:
    woerter = _woerter(teil)
    ganz = f"{zusammenhang} {teil}"
    nummern = _nummern(ganz)
    mehrere = re.search(r"\b(beide|beiden|alle|allen|jede|jeder)\b", ganz.lower())
    ids = {g.id for g in gebiete}

    # 1. Eine vorhandene Karte, die gut passt. Zählt: Wörter der Karte, und doppelt die
    # Wörter der Provinz ("Ausgangsrechnungen der GbR" gehört zur Gilde, nicht zur Werkstatt).
    nach_id = {g.id: g for g in gebiete}
    provinz_punkte = {gid: _punkte(_woerter(ganz), nach_id[gid]) for gid in ids}

    def bewerten(woerter: set[str]) -> list[tuple[int, dict]]:
        bewertet = [
            (k + 2 * provinz_punkte[s["gebiet"]], k, s)
            for s in stuecke
            if s["gebiet"] in ids and (k := _karten_punkte(woerter, s)) >= 2
        ]
        return [(p, s) for p, k, s in bewertet if k >= 3 or p >= 4]

    # "Belege zu Arbeitszimmer": Das Arbeitszimmer sagt, welche Karte; "Belege" nur, wenn
    # sonst nichts passt
    kopf = _KOPF.match(teil)
    bewertet = (kopf and bewerten(_woerter(teil[kopf.end(1) :]))) or bewerten(woerter)
    if bewertet:
        beste = max(p for p, _ in bewertet)
        karte = next(s for p, s in bewertet if p == beste)
        provinz = _provinz(karte["gebiet"])
        geschwister = [g for g in gebiete if _provinz(g.id) == provinz]
        if nummern:
            ziel = [g for g in geschwister if g.nummer in nummern] or [
                g for g in geschwister if g.id == karte["gebiet"]
            ]
        elif mehrere:
            ziel = geschwister
        else:
            ziel = [g for g in geschwister if g.id == karte["gebiet"]]
        treffer = []
        for g in ziel:
            hier = [(p, s) for p, s in bewertet if s["gebiet"] == g.id and p == beste]
            treffer.append(hier[0][1]["id"] if hier else None)
        return Wunsch(teil, tuple(g.id for g in ziel), tuple(treffer))

    # 2. Keine Karte: die Provinz, zu der die Wörter (mit Zusammenhang) am besten passen
    punkte = {g.id: _punkte(_woerter(ganz), g) for g in gebiete}
    beste = max(punkte.values(), default=0)
    if beste == 0:
        ziel_ids = [next((g.id for g in gebiete if g.provinz.key == "burg"), gebiete[0].id)]
    else:
        ziel = [g for g in gebiete if punkte[g.id] == beste]
        if nummern:
            ziel = [g for g in ziel if g.nummer in nummern] or ziel
        elif not mehrere:
            ziel = ziel[:1]
        ziel_ids = [g.id for g in ziel]
    return Wunsch(teil, tuple(ziel_ids), tuple(None for _ in ziel_ids))
