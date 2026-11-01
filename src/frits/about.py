"""The "Über FRITS" page: who made FRITS, for whom, the podcast, and how to report a bug.

The texts live here (not in the HTML) so they are easy to find and change.
A text that starts with "[Platzhalter" is shown greyed out until Marie sends the real one.
"""

from __future__ import annotations

import platform

from frits import __version__

BUG_ADDRESS = "hallo@kilg.de"
BUG_SUBJECT_TAG = "[FRITS Fehler]"

ABOUT = {
    "marie": (
        "[Platzhalter: Zwei, drei Sätze über Marie. Wer sie ist und warum sie FRITS gebaut hat.]"
    ),
    "fritz": "[Platzhalter: Ein paar Sätze über Fritz, für den FRITS gemacht ist.]",
    "podcast_name": "[Platzhalter: Name des KI-Podcasts]",
    "podcast_text": "[Platzhalter: Ein Satz, worum es im Podcast geht.]",
    "podcast_link": "",  # e.g. "https://…"; empty means: no button yet
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
