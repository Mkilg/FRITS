"""Fritte stutzt: Passt diese Datei überhaupt zu der Karte?

Das Spiel soll nicht alles glauben. Wer ein Urlaubsfoto auf "Steuer-ID" legt, bekommt sonst
Gold, Fortschritt und am Ende ein Urlaubsfoto im Ordner für die Beraterin.

Geprüft wird nur hier auf dem Computer, ohne KI:
- der Dateiname ("urlaubsfoto.png", "Strand.jpg"),
- ob der Name viel eher zu einer anderen Karte passt,
- bei Bildern: ob überhaupt Text darauf steht (Texterkennung wie beim Beleg-Lesen).

Es ist nur eine Rückfrage, nie ein Verbot: "Trotzdem ablegen" geht immer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from frits.steuer import spiel

# Wörter in Dateinamen, die nach einem privaten Foto klingen. Bewusst kurz: "Familienkasse",
# "Hundesteuer" und "Babysitter" sind echte Steuer-Unterlagen und stehen darum nicht hier.
PRIVAT = (
    "urlaub",
    "ferien",
    "strand",
    "selfie",
    "hochzeit",
    "geburtstag",
    "sonnenuntergang",
    "sunset",
    "beach",
    "holiday",
    "vacation",
    "wedding",
    "birthday",
    "wallpaper",
    "meme",
)
# Ausnahmen: echte Unterlagen, die mit einem der Wörter anfangen
_ECHT = ("urlaubsgeld", "urlaubsanspruch", "urlaubsbescheinigung", "ferienjob", "ferienwohnung")

# Konten, die nicht das Geschäftskonto sind (Lena, Runde 78: die Spürnase legte Sparkonto-,
# Tagesgeld- und Kreditkarten-Auszüge still zu „Kontoauszüge Geschäftskonto“).
# „Sparkasse“ ist eine Bank und steht darum nicht hier.
# Lena (Runde 81): „Sparbuchung“ ist kein Sparbuch, und eine Geschäftskarte ist geschäftlich.
# Lena (Runde 83): nur Karten und Konten, nicht „Geschäftsjahr“ oder „Firmenjubiläum“
_KEIN_PRIVATES_KONTO = (
    "sparbuchung",
    "geschaeftskart",
    "geschaeftskredit",
    "firmenkart",
    "firmenkredit",
    "businesscard",
    "businesskredit",
    "corporatecard",
)
PRIVATE_KONTEN = {
    "sparkonto": "Sparkonto",
    "sparbuch": "Sparbuch",
    "tagesgeld": "Tagesgeld",
    "festgeld": "Festgeld",
    "kreditkart": "Kreditkarte",
    "depot": "Depot",
    "privatkonto": "Privatkonto",
}

BILDER = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
MIN_WOERTER = 3  # so viele lesbare Wörter hat selbst ein Parkschein


@dataclass(frozen=True)
class Zweifel:
    grund: str  # "privat", "andere_karte" oder "kein_text"
    text: str  # was Fritte fragt
    # Die Karte, nach der der Name klingt (nur bei "andere_karte"): Dorthin führt ein Knopf
    besser: dict | None = None
    # Kurz für den Kasten am Zettel, wo der Dateiname schon steht (Lena, Runde 83)
    kurz: str = ""


def _tokens(name: str) -> list[str]:
    return re.findall(r"[a-z]+", name.lower().translate(spiel._UMLAUTE))


def privat_wort(name: str) -> str | None:
    """Das Wort im Dateinamen, das nach einem privaten Foto klingt."""
    for t in _tokens(Path(name).stem):
        if t.startswith(_ECHT):
            continue
        for w in PRIVAT:
            if t.startswith(w) or (len(w) >= 6 and w in t):
                return w
    return None


def lesbare_woerter(pfad: Path) -> int | None:
    """Wie viele echte Wörter auf einem Bild stehen. None, wenn das nicht zu prüfen ist
    (keine Texterkennung installiert, Format nicht lesbar): dann fragt Fritte nicht."""
    try:
        import pytesseract
        from PIL import Image, ImageOps

        if os.environ.get("FRITS_TESSERACT"):
            pytesseract.pytesseract.tesseract_cmd = os.environ["FRITS_TESSERACT"]
        with Image.open(pfad) as img:
            img.thumbnail((1600, 1600))
            text = pytesseract.image_to_string(ImageOps.grayscale(img), lang="deu+eng", timeout=10)
    except Exception:  # noqa: BLE001 — lieber nicht fragen als die Ablage blockieren
        return None
    woerter = re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", text)
    return sum(1 for w in woerter if re.search(r"[aeiouäöüy]", w.lower()))


def nennt_die_karte(original: str, quellen: str) -> bool:
    """Nennt die Karte diese Unterlage selbst als Quelle? ("Steht auf der Lohnsteuerbescheinigung"
    und die Datei heißt "Lohnsteuerbescheinigung 2025.pdf")"""
    text = " ".join(_tokens(quellen))
    return any(len(t) >= 6 and t in text for t in _tokens(Path(original).stem))


def pruefe(
    pfad: Path,
    original: str,
    stueck: dict,
    alle: list[dict],
    bild_lesen: bool = True,
    quellen: str = "",
    ausdruecklich: bool = False,
) -> Zweifel | None:
    """Gibt einen Zweifel zurück, wenn die Datei offensichtlich nicht zur Karte passt.

    quellen: was die Karte selbst als Quelle nennt; nennt sie diese Unterlage, gibt es keinen
    Zweifel. ausdruecklich: Die Karte wurde bewusst gewählt (Menü im Eingangskorb); dann rät
    Fritte nicht mehr nach dem Namen, ob eine andere Karte besser passt."""
    karte = stueck["name"]
    # Vor der Quellen-Prüfung: „Sparkonto Kontoauszug.pdf“ nennt die Karte und ist doch privat
    tokens = _tokens(Path(original).stem)
    # „Business Card“ und „Geschäfts-Kreditkarte“ sind zwei Wörter, darum auch zusammengezogen
    kette = "".join(tokens)
    geschaeftlich = any(t.startswith(_KEIN_PRIVATES_KONTO) for t in tokens) or any(
        k in kette for k in _KEIN_PRIVATES_KONTO[1:]
    )
    if stueck.get("key") == "kontoauszuege" and not geschaeftlich:
        konto = next(
            (n for t in tokens for w, n in PRIVATE_KONTEN.items() if t.startswith(w)), None
        )
        if konto:
            return Zweifel(
                "privates_konto",
                f"„{original}“ klingt nach {konto}. Gehört das wirklich zu „{karte}“?",
                kurz=f"Klingt nach {konto}. Wirklich „{karte}“?",
            )
    if quellen and nennt_die_karte(original, quellen):
        return None
    wort = privat_wort(original)
    if wort:
        return Zweifel(
            "privat",
            f"„{original}“ klingt nach einem privaten Foto, nicht nach „{karte}“.",
            kurz=f"Klingt privat. Wirklich „{karte}“?",
        )
    # Passt der Name klar zu einer anderen Karte, aber gar nicht zu dieser?
    if not ausdruecklich and spiel.ahnung(original, [stueck]).stueck is None:
        andere = spiel.ahnung(original, alle).stueck
        if andere is not None and andere["id"] != stueck["id"] and andere["name"] != karte:
            return Zweifel(
                "andere_karte",
                f"„{original}“ klingt eher nach „{andere['name']}“ als nach „{karte}“.",
                besser=andere,
                kurz=f"Klingt eher nach „{andere['name']}“ als nach „{karte}“.",
            )
    if bild_lesen and Path(original).suffix.lower() in BILDER:
        n = lesbare_woerter(pfad)
        if n is not None and n < MIN_WOERTER:
            return Zweifel(
                "kein_text",
                f"Auf „{original}“ finde ich keinen Text. Das sieht aus wie ein Foto,"
                f" nicht wie „{karte}“.",
                kurz=f"Kein Text darauf. Wirklich „{karte}“?",
            )
    return None
