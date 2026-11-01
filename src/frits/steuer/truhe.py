"""Die Schatztruhe: ein Zip mit Passwort für die Steuerberaterin, und die passende Mail.

Das Zip ist mit AES-256 verschlüsselt. Das Passwort steht NICHT in der Mail; es geht
getrennt an die Beraterin (z. B. per SMS). Abgeschickt wird nichts von FRITS selbst:
Die Mail öffnet sich im Mail-Programm, und du drückst dort auf Senden.
"""

from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path

import pyzipper

from frits.steuer.katalog import Gebiet, Profil

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


def passwort() -> str:
    """Z. B. "Wiese-Anker-Eule-Kahn-Moos-47": rund 40 Bit Zufall, trotzdem leicht vorzulesen."""
    teile = [secrets.choice(_WOERTER).capitalize() for _ in range(5)]
    return "-".join(teile) + f"-{secrets.randbelow(90) + 10}"


_STATUS_TEXT = {
    "gesammelt": "dabei",
    "gibts_nicht": "gibt es dieses Jahr nicht",
    "fragen": "Frage an dich",
    "offen": "fehlt noch",
}


def inhaltsverzeichnis(
    profil: Profil, gebiete: list[Gebiet], stuecke: list[dict], dateien: list[dict]
) -> str:
    zeilen = [
        f"Steuerunterlagen {profil.jahr}" + (f" von {profil.name}" if profil.name else ""),
        "",
    ]
    for nr, g in enumerate(gebiete, start=1):
        eigene = [s for s in stuecke if s["gebiet"] == g.id]
        if not eigene:
            continue
        zeilen.append(f"{nr:02d} {g.bereich}")
        for s in eigene:
            zeilen.append(f"   - {s['name']}: {_STATUS_TEXT[s['status']]}")
            for d in dateien:
                if d["stueck_id"] == s["id"]:
                    zeilen.append(f"       {Path(d['pfad']).name}")
            if s["frage"]:
                zeilen.append(f"       Frage: {s['frage']}")
        zeilen.append("")
    zeilen.append(f"Erstellt am {date.today().strftime('%d.%m.%Y')} mit FRITS.")
    return "\n".join(zeilen)


def _ordnername(nr: int, g: Gebiet) -> str:
    return f"{nr:02d} {g.bereich.replace('/', '-').replace(' · ', ' ')}"


def packen(
    ziel: Path,
    profil: Profil,
    gebiete: list[Gebiet],
    stuecke: list[dict],
    dateien: list[dict],
    kennwort: str,
) -> Path:
    """Baut das Zip: ein Ordner pro Gebiet, dazu das Inhaltsverzeichnis."""
    ziel = Path(ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ordner_von = {g.id: _ordnername(nr, g) for nr, g in enumerate(gebiete, start=1)}
    gebiet_von = {s["id"]: s["gebiet"] for s in stuecke}
    tmp = ziel.with_suffix(".zip.tmp")
    with pyzipper.AESZipFile(
        tmp, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(kennwort.encode("utf-8"))
        zf.writestr(
            "00 Inhaltsverzeichnis.txt",
            inhaltsverzeichnis(profil, gebiete, stuecke, dateien).encode("utf-8"),
        )
        for d in dateien:
            pfad = Path(d["pfad"])
            gebiet = gebiet_von.get(d["stueck_id"])
            if gebiet not in ordner_von or not pfad.is_file():
                continue
            zf.write(pfad, f"{ordner_von[gebiet]}/{pfad.name}")
    tmp.replace(ziel)
    return ziel


def mail(profil: Profil, gebiete: list[Gebiet], stuecke: list[dict]) -> tuple[str, str]:
    """Betreff und Text der Mail an die Beraterin. Du kannst alles ändern, bevor du sendest."""
    dabei = [
        g.bereich
        for g in gebiete
        if any(s["gebiet"] == g.id and s["status"] == "gesammelt" for s in stuecke)
    ]
    gibts_nicht = [s["name"] for s in stuecke if s["status"] == "gibts_nicht"]
    fragen = [s for s in stuecke if s["status"] == "fragen"]
    offen = [s["name"] for s in stuecke if s["status"] == "offen"]
    bereich = {g.id: g.bereich for g in gebiete}

    zeilen = [
        "Hallo,",
        "",
        (
            f"anbei meine Unterlagen für {profil.jahr} als Zip-Datei. "
            "Das Passwort schicke ich dir getrennt."
        ),
        (
            "Die Datei ist mit AES verschlüsselt; falls sie sich nicht per Doppelklick öffnen "
            "lässt, klappt es mit 7-Zip."
        ),
        "",
    ]
    if dabei:
        zeilen += ["Drin sind: " + ", ".join(dabei) + ". Ein Inhaltsverzeichnis liegt bei.", ""]
    if gibts_nicht:
        zeilen += ["Gibt es dieses Jahr nicht: " + ", ".join(gibts_nicht) + ".", ""]
    if fragen:
        zeilen.append("Meine Fragen:")
        for i, s in enumerate(fragen, start=1):
            text = s["frage"] or f"Zu „{s['name']}“ bin ich unsicher. Brauchst du das?"
            zeilen.append(f"{i}. {bereich.get(s['gebiet'], '')}: {text}")
        zeilen.append("")
    if offen:
        zeilen += ["Noch nicht gefunden habe ich: " + ", ".join(offen) + ".", ""]
    zeilen += ["Viele Grüße", profil.name or ""]
    return f"Steuerunterlagen {profil.jahr}", "\n".join(zeilen).rstrip() + "\n"
