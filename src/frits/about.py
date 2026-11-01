"""The "Über FRITS" page: who made FRITS, for whom, the podcast, and how to report a bug.

The texts live here (not in the HTML) so they are easy to find and change.
A text that starts with "[Platzhalter" is shown greyed out, as a reminder that it is unfinished.
"""

from __future__ import annotations

import platform

from frits import __version__

BUG_ADDRESS = "hallo@kilg.de"
BUG_SUBJECT_TAG = "[FRITS Fehler]"

ABOUT = {
    "projekt": [
        (
            "FRITS ist ein experimentelles, nicht-kommerzielles Projekt. "
            "Marie entwickelt es für ihren Freund Fritz."
        ),
        (
            "Fritz ist von Buchhaltung ziemlich gestresst, und Marie kann das gut nachfühlen. "
            "Deshalb soll FRITS den Papierkram leichter machen: Rechnungen, Angebote, "
            "Erinnerungen und die Steuer, Schritt für Schritt."
        ),
        (
            "FRITS ist noch in Arbeit. Wenn etwas hakt oder komisch aussieht, "
            'sag gern Bescheid, unten unter "Fehler melden".'
        ),
    ],
    "hinweis": [
        (
            "FRITS ist ein kostenloses, nicht-kommerzielles Hobbyprojekt. Es hilft dir, deine "
            "eigenen Rechnungen und Unterlagen zu ordnen, und rechnet Zahlen für dich zusammen."
        ),
        (
            "FRITS ist keine Steuer-, Rechts- oder Finanzberatung und ersetzt sie nicht. "
            "Prüf bitte alle Zahlen, bevor du sie ans Finanzamt schickst, und frag im Zweifel "
            "deine Steuerberatung."
        ),
        (
            "Du nutzt FRITS auf eigene Verantwortung. Es gilt die MIT-Lizenz; für Schäden haftet "
            "Marie nur bei Vorsatz und grober Fahrlässigkeit. Welche Programme anderer Leute in "
            "FRITS stecken, steht in LIZENZEN.txt neben der App."
        ),
    ],
    "podcast_name": "Der KI-Podcast der ARD",
    "podcast_text": (
        "FRITS ist für eine Live-Folge vom ARD KI-Podcast entstanden. "
        "Im Podcast besprechen Gregor Schmalzried, Marie Kilg und Fritz Espenlaub jeden Dienstag, "
        "was hinter den wichtigsten KI-Entwicklungen steckt. "
        "Eine ARD-Produktion von BR24 und SWR."
    ),
    "podcast_link": "https://www.ardsounds.de/sendung/der-ki-podcast/urn:ard:show:65505255c703e51e/",
}


def is_placeholder(text: str) -> bool:
    return text.startswith("[Platzhalter")


def system_info() -> str:
    """Which computer FRITS runs on, e.g. "macOS 14.5 (arm64)". Nothing personal."""
    mac = platform.mac_ver()[0]
    name = f"macOS {mac}" if mac else f"{platform.system()} {platform.release()}".strip()
    machine = platform.machine()
    return f"{name} ({machine})" if machine else name


def bug_email(version: str = __version__, system: str | None = None) -> tuple[str, str]:
    """Subject and body of the bug report. Only the version and the computer type are filled in:
    no names, no customers, no amounts. Fritz writes the rest himself in Mail."""
    subject = f"{BUG_SUBJECT_TAG} Version {version}"
    body = "\n".join(
        [
            "Hallo Marie,",
            "",
            "in FRITS ist mir etwas aufgefallen.",
            "",
            "Was wollte ich tun?",
            "",
            "",
            "Was ist stattdessen passiert?",
            "",
            "",
            "(Ein Bildschirmfoto hilft sehr. Bitte keine Kundennamen, Adressen oder Kontonummern.)",
            "",
            "---",
            f"FRITS-Version: {version}",
            f"Computer: {system or system_info()}",
        ]
    )
    return subject, body
