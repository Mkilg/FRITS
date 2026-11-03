"""Die Schatztruhe: ein Zip für die Steuerberaterin, und die passende Mail.

Du wählst, wie die Truhe verschlossen wird:
- mit Passwort (empfohlen): AES-256. Das Passwort steht NICHT in der Mail; es geht
  getrennt an die Beraterin (z. B. per SMS). Außen sieht man nur "Unterlagen-<jahr>.zip",
  die echten Dateinamen liegen im inneren Zip.
- ohne Passwort: ein normales Zip, das sich überall per Doppelklick öffnet. Dafür kann
  jeder es lesen, der die Mail in die Hände bekommt.
Abgeschickt wird nichts von FRITS selbst: Die Mail öffnet sich im Mail-Programm, und du
drückst dort auf Senden.
"""

from __future__ import annotations

import re
import secrets
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pyzipper

from frits.steuer.katalog import MACHT_BERATERIN, Gebiet, Profil, ist_kern

# Kurze, gut diktierbare Wörter ohne Umlaute, damit das Passwort per SMS und Telefon klappt.
_WOERTER = [
    "apfel",
    "ast",
    "bach",
    "baum",
    "beere",
    "berg",
    "birne",
    "blatt",
    "blume",
    "boot",
    "brot",
    "brunnen",
    "burg",
    "dach",
    "drache",
    "eiche",
    "eule",
    "fahne",
    "farn",
    "feder",
    "feld",
    "fels",
    "fisch",
    "fluss",
    "fuchs",
    "garten",
    "gras",
    "hafen",
    "hase",
    "heu",
    "hirsch",
    "honig",
    "hut",
    "igel",
    "insel",
    "kahn",
    "kerze",
    "kiesel",
    "kirsche",
    "klee",
    "korb",
    "krone",
    "kuchen",
    "laterne",
    "linde",
    "loewe",
    "moos",
    "muehle",
    "nebel",
    "nuss",
    "ofen",
    "otter",
    "pfad",
    "pilz",
    "quelle",
    "rabe",
    "regen",
    "riese",
    "rose",
    "sand",
    "schaf",
    "schiff",
    "schild",
    "see",
    "segel",
    "sonne",
    "stern",
    "stein",
    "strand",
    "tal",
    "tanne",
    "teich",
    "turm",
    "ufer",
    "vogel",
    "wald",
    "welle",
    "wiese",
    "wind",
    "wolke",
    "zelt",
    "zweig",
    "kartoffel",
    "fritte",
    "siegel",
    "truhe",
    "karte",
    "kompass",
    "anker",
    "leuchtturm",
]


# Für die Code-Blöcke: ohne 0/O, 1/I/L, damit beim Abtippen nichts verwechselt wird
_ZEICHEN = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def passwort() -> str:
    """Z. B. "Wiese-Anker-Eule-Kahn-Moos-K7PM-4XQR": 5 Wörter (rund 32 Bit) und zwei Blöcke
    aus je 4 Zeichen (rund 40 Bit), zusammen über 70 Bit Zufall. Das hält auch, wenn eine
    Truhe verloren geht: Zip-AES bremst Rateversuche kaum, also muss das Passwort selbst
    lang sein. Trotzdem lässt es sich vorlesen und von Hand abschreiben."""
    woerter = [secrets.choice(_WOERTER).capitalize() for _ in range(5)]
    bloecke = ["".join(secrets.choice(_ZEICHEN) for _ in range(4)) for _ in range(2)]
    return "-".join(woerter + bloecke)


_STATUS_TEXT = {
    "gesammelt": "dabei",
    "gibts_nicht": "gibt es dieses Jahr nicht",
    "fragen": "offene Frage (steht in der Mail)",
    "offen": "fehlt noch",
}


@dataclass(frozen=True)
class Nachtrag:
    """Die vollständige Kiste nach einer halben Sendung über den Notausgang (Lena, Runde 47/48):
    Mail, Zip und Inhaltsverzeichnis sagen, dass sie die früheren ersetzt und was neu ist."""

    datum: str  # die letzte halbe Sendung, "2. November 2026"
    mehrere: bool = False  # gab es mehr als eine halbe Sendung?
    neu: tuple[str, ...] = ()  # Karten mit Belegen, die seitdem dazukamen
    neue_dateien: frozenset[int] = field(default_factory=frozenset)

    def ersetzt(self) -> str:
        if self.mehrere:
            return f"meine bisherigen Sendungen (zuletzt vom {self.datum})"
        return f"meine Sendung vom {self.datum}"


def inhaltsverzeichnis(
    profil: Profil,
    gebiete: list[Gebiet],
    stuecke: list[dict],
    dateien: list[dict],
    verweise: list[dict] | None = None,
    nachtrag: Nachtrag | None = None,
) -> str:
    """Was in der Truhe liegt, nach Bereich. Bei jeder Datei steht ihr ursprünglicher Name,
    und mitbenutzte Dateien (eine Mietliste für zwei Wohnungen) sagen, wo sie liegen."""
    verweise = verweise or []
    bereich = {g.id: g.bereich for g in gebiete}
    zeilen = [
        f"Steuerunterlagen {profil.jahr}" + (f" von {profil.name}" if profil.name else ""),
        "",
    ]
    if nachtrag:
        zeilen[-1:] = [
            f"Vollständiger Stand. Ersetzt {nachtrag.ersetzt()}."
            + (" Was seitdem dazukam, ist mit (neu) markiert." if nachtrag.neue_dateien else ""),
            "",
        ]
    for nr, g in enumerate(gebiete, start=1):
        eigene = [s for s in stuecke if s["gebiet"] == g.id]
        if not eigene:
            continue
        kopf = len(zeilen)
        zeilen.append(f"{nr:02d} {g.bereich}")
        for s in eigene:
            if s.get("art") == "bonus" and s["status"] == "offen" and not s.get("wunsch"):
                continue  # nicht gesammelte Bonus-Schätze sind kein Thema, außer sie wünscht sie
            status = _STATUS_TEXT[s["status"]]
            if s["status"] == "offen" and s.get("wunsch"):
                status = "fehlt noch (gewünscht)"
            if s["status"] == "fragen" and s["frage"] == MACHT_BERATERIN:
                status = "erstellt die Beraterin"
            zeilen.append(f"   - {s['name']}: {status}")
            for d in dateien:
                if d["stueck_id"] == s["id"]:
                    name = Path(d["pfad"]).name
                    original = d.get("original") or ""
                    zusatz = f" (Original: {original})" if original and original != name else ""
                    if nachtrag and d.get("id") in nachtrag.neue_dateien:
                        zusatz += " (neu)"
                    zeilen.append(f"       {name}{zusatz}")
            for v in verweise:
                if v["stueck_id"] == s["id"]:
                    wo = bereich.get(v["bei_gebiet"], v["bei_gebiet"])
                    zeilen.append(f"       dieselbe Datei wie bei {wo}: {Path(v['pfad']).name}")
            if s["frage"] and s["frage"] != MACHT_BERATERIN:
                frage = siezen(s["frage"]) if profil.anrede == "sie" else s["frage"]
                zeilen.append(f"       Frage: {frage}")
        if len(zeilen) == kopf + 1:
            del zeilen[kopf]  # Lena (Runde 48): kein Bereich ohne eine Zeile darunter
            continue
        zeilen.append("")
    zeilen.append(f"Erstellt am {date.today().strftime('%d.%m.%Y')} mit FRITS.")
    return "\n".join(zeilen)


def ordnername(nr: int, g: Gebiet) -> str:
    """Der Ordner dieses Gebiets im Zip, z. B. \"03 Selbstständig\"."""
    return f"{nr:02d} {g.bereich.replace('/', '-').replace(' · ', ' ')}"


def packen(
    ziel: Path,
    profil: Profil,
    gebiete: list[Gebiet],
    stuecke: list[dict],
    dateien: list[dict],
    kennwort: str | None,
    verweise: list[dict] | None = None,
    nachtrag: Nachtrag | None = None,
) -> Path:
    """Baut das Zip: ein Ordner pro Gebiet, dazu das Inhaltsverzeichnis.

    Ohne Kennwort (None) wird es ein normales Zip ohne Verschlüsselung.
    Mit Kennwort liegt das Zip noch einmal in einem verschlossenen Zip. Zip-AES verschlüsselt
    nur den Inhalt, nicht die Dateinamen; so steht außen nur "Unterlagen-<jahr>.zip" und
    niemand liest "Lohnsteuerbescheinigung" oder Ordnernamen ohne das Passwort."""
    ziel = Path(ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_suffix(".zip.tmp")
    if not kennwort:
        _packe_unterlagen(tmp, profil, gebiete, stuecke, dateien, verweise, nachtrag)
        tmp.replace(ziel)
        return ziel
    innen = ziel.with_suffix(".innen.tmp")
    try:
        _packe_unterlagen(innen, profil, gebiete, stuecke, dateien, verweise, nachtrag)
        with pyzipper.AESZipFile(
            tmp, "w", compression=pyzipper.ZIP_STORED, encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(kennwort.encode("utf-8"))
            zf.write(innen, innen_name(profil.jahr, vollstaendig=nachtrag is not None))
    finally:
        innen.unlink(missing_ok=True)
    tmp.replace(ziel)
    return ziel


def innen_name(jahr: int, vollstaendig: bool = False) -> str:
    """Der einzige Name, den man außen an einer verschlossenen Truhe sieht. Ein Nachtrag
    heißt anders, sonst überschreibt er beim Entpacken die erste Sendung (Lena, Runde 48)."""
    return f"Unterlagen-{jahr}{'-vollstaendig' if vollstaendig else ''}.zip"


def _packe_unterlagen(
    ziel: Path,
    profil: Profil,
    gebiete: list[Gebiet],
    stuecke: list[dict],
    dateien: list[dict],
    verweise: list[dict] | None,
    nachtrag: Nachtrag | None = None,
) -> None:
    ordner_von = {g.id: ordnername(nr, g) for nr, g in enumerate(gebiete, start=1)}
    gebiet_von = {s["id"]: s["gebiet"] for s in stuecke}
    with zipfile.ZipFile(ziel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "00 Inhaltsverzeichnis.txt",
            inhaltsverzeichnis(profil, gebiete, stuecke, dateien, verweise, nachtrag).encode(
                "utf-8"
            ),
        )
        for d in dateien:
            pfad = Path(d["pfad"])
            gebiet = gebiet_von.get(d["stueck_id"])
            if gebiet not in ordner_von or not pfad.is_file():
                continue
            zf.write(pfad, f"{ordner_von[gebiet]}/{pfad.name}")


def verschluesselt(pfad: Path) -> bool:
    """Ist diese Truhe mit Passwort verschlossen? Steht im Zip selbst (Bit 0 der Flags)."""
    try:
        with zipfile.ZipFile(pfad) as zf:
            return any(info.flag_bits & 0x1 for info in zf.infolist())
    except (OSError, zipfile.BadZipFile):
        return False


_SIE = [
    (r"\bhast du\b", "haben Sie"),
    (r"\bbrauchst du\b", "brauchen Sie"),
    (r"\bkannst du\b", "können Sie"),
    (r"\bweißt du\b", "wissen Sie"),
    (r"\bmachst du\b", "machen Sie"),
    (r"\berstellst du\b", "erstellen Sie"),
    (r"\bsiehst du\b", "sehen Sie"),
    (r"\bdir\b", "Ihnen"),
    (r"\bdich\b", "Sie"),
    (r"\bdeinem\b", "Ihrem"),
    (r"\bdeinen\b", "Ihren"),
    (r"\bdeiner\b", "Ihrer"),
    (r"\bdeine\b", "Ihre"),
    (r"\bdein\b", "Ihr"),
]


def siezen(text: str) -> str:
    """Macht aus den vorformulierten Fragen ("Hast du sie noch vom letzten Jahr?") die
    Sie-Form. Nur Wendungen, die sicher sind; ein einzelnes "du" bleibt, wie es ist."""
    for muster, ersatz in _SIE:

        def passend(m: re.Match, ersatz: str = ersatz) -> str:
            return ersatz[:1].upper() + ersatz[1:] if m.group(0)[:1].isupper() else ersatz

        text = re.sub(muster, passend, text, flags=re.IGNORECASE)
    return text


def _nach_bereich(eintraege: list[tuple[str, str]]) -> list[str]:
    """[(Bereich, Name), ...] wird zu "- Bereich: Name, Name", ohne Doppelte."""
    gruppen: dict[str, list[str]] = {}
    for bereich, name in eintraege:
        liste = gruppen.setdefault(bereich, [])
        if name not in liste:
            liste.append(name)
    return [f"- {b}: {', '.join(namen)}" for b, namen in gruppen.items()]


def mail(
    profil: Profil,
    gebiete: list[Gebiet],
    stuecke: list[dict],
    mit_passwort: bool = True,
    nachtrag: Nachtrag | None = None,
) -> tuple[str, str]:
    """Betreff und Text der Mail an die Beraterin. Du kannst alles ändern, bevor du sendest.

    Alles ist nach Bereich geordnet ("Vermietete Wohnung · Lindenstraße: Grundsteuerbescheid"),
    damit sie weiß, welche Wohnung gemeint ist. Anrede: du oder Sie, wie im Profil gewählt."""
    sie = profil.anrede == "sie"
    dir_ = "Ihnen" if sie else "dir"
    name = profil.beraterin_name.strip()
    if sie:
        gruss = f"Guten Tag {name}," if name else "Guten Tag,"
    else:
        gruss = f"Hallo {name}," if name else "Hallo,"
    bereich = {g.id: g.bereich for g in gebiete}
    reihenfolge = {g.id: i for i, g in enumerate(gebiete)}
    stuecke = sorted(stuecke, key=lambda s: reihenfolge.get(s["gebiet"], 999))

    def wo(s: dict) -> str:
        return bereich.get(s["gebiet"], s["gebiet"])

    dabei = [
        g.bereich
        for g in gebiete
        if any(s["gebiet"] == g.id and s["status"] == "gesammelt" for s in stuecke)
    ]
    pruefen = [
        (wo(s), s["name"])
        for s in stuecke
        if s["status"] == "gibts_nicht" and ist_kern(s["gebiet"], s["key"])
    ]
    gibts_nicht = [
        (wo(s), s["name"])
        for s in stuecke
        if s["status"] == "gibts_nicht" and not ist_kern(s["gebiet"], s["key"])
    ]
    macht_sie = [
        (wo(s), s["name"])
        for s in stuecke
        if s["status"] == "fragen" and s["frage"] == MACHT_BERATERIN
    ]
    # Auch eine Frage zu einem Beleg, der schon da ist, kommt in die Mail
    fragen = [
        s
        for s in stuecke
        if s["frage"] != MACHT_BERATERIN and (s["status"] == "fragen" or s["frage"])
    ]
    # Was sie sich gewünscht hat, geht nie verloren, auch auf Bonus-Karten
    von_ihr = [(wo(s), s["name"]) for s in stuecke if s["status"] == "offen" and s.get("wunsch")]
    offen = [
        (wo(s), s["name"])
        for s in stuecke
        if s["status"] == "offen" and s.get("art") != "bonus" and not s.get("wunsch")
    ]

    zeilen = [gruss, "", f"anbei meine Unterlagen für {profil.jahr} als Zip-Datei."]
    if nachtrag:
        brauchst = "brauchen Sie" if sie else "brauchst du"
        frueher = "früheren Zip-Dateien" if nachtrag.mehrere else "erste Zip-Datei"
        zeilen[-1] = (
            f"anbei der vollständige Stand meiner Unterlagen für {profil.jahr} als Zip-Datei."
            f" Er ersetzt {nachtrag.ersetzt()}, die {frueher} {brauchst} nicht mehr."
        )
    if mit_passwort:
        zeilen[-1] += f" Das Passwort schicke ich {dir_} getrennt."
        zeilen.append(
            "Die Datei ist mit AES verschlüsselt; falls sie sich nicht per Doppelklick "
            "öffnen lässt, klappt es mit 7-Zip (Windows) oder Keka (Mac). Darin liegt noch ein Zip mit den "
            "Unterlagen, das sich dann ohne Passwort öffnet."
        )
    zeilen.append("")
    if nachtrag and nachtrag.neu:
        # Lena (Runde 48): Die Beraterin sollte nicht zwei Listen vergleichen müssen
        zeilen += ["Neu dabei seit der letzten Sendung: " + ", ".join(nachtrag.neu) + ".", ""]
    if dabei:
        zeilen += ["Drin sind: " + ", ".join(dabei) + ". Ein Inhaltsverzeichnis liegt bei.", ""]
    if macht_sie:
        wie = "erstellen Sie" if sie else "erstellst du"
        zeilen += [f"Wie bisher {wie}:", *_nach_bereich(macht_sie), ""]
    if pruefen:
        bitte = "Sagen Sie mir bitte" if sie else "Sag mir bitte"
        zeilen += [
            "Bitte prüfen: Diese Unterlagen habe ich nicht, obwohl man sie meistens braucht.",
            *_nach_bereich(pruefen),
            f"{bitte}, ob das so passt.",
            "",
        ]
    if gibts_nicht:
        zeilen += ["Gibt es dieses Jahr nicht:", *_nach_bereich(gibts_nicht), ""]
    if fragen:
        zeilen.append("Meine Fragen:")
        brauchst = "Brauchen Sie das?" if sie else "Brauchst du das?"
        gesehen: set[str] = set()
        nummer = 0
        for s in fragen:
            if s["frage"]:
                text = s["frage"]
            elif ist_kern(s["gebiet"], s["key"]):
                # Lena (Runde 57): Bei der Jahressteuerbescheinigung heißt „Lieber fragen“ meist
                # „hab ich noch nicht“, nicht „brauchst du das?“. Die hat fast jede:r.
                text = "Habe ich noch nicht. Reicht es, wenn ich es nachreiche?"
            else:
                text = f"Ich bin unsicher. {brauchst}"
            if sie:
                text = siezen(text)
            zeile = f"{wo(s)}, {s['name']}: {text}"
            if zeile in gesehen:
                continue
            gesehen.add(zeile)
            nummer += 1
            zeilen.append(f"{nummer}. {zeile}")
        zeilen.append("")
    if von_ihr:
        liste = "Ihrer" if sie else "deiner"
        zeilen += [f"Noch offen von {liste} Liste:", *_nach_bereich(von_ihr), ""]
    if offen:
        zeilen += ["Noch nicht gefunden habe ich:", *_nach_bereich(offen), ""]
    zeilen += ["Viele Grüße", profil.name or ""]
    betreff = f"Steuerunterlagen {profil.jahr}"
    if nachtrag:
        # „vollständig“ nach vorn: Am Handy wurde der Betreff abgeschnitten (Lena, Runde 48)
        betreff += " – vollständig (ersetzt " + (
            "frühere Sendungen)" if nachtrag.mehrere else "die erste Sendung)"
        )
    return betreff, "\n".join(zeilen).rstrip() + "\n"
