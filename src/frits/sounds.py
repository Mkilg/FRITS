"""Lists FRITS's sound cues and focus music so the web app can serve them.

The files live in src/frits/assets/sounds/ and are made by tools/make_sounds.py.
They are self-generated (no samples from anyone else), so they share FRITS's MIT
license; see assets/sounds/LICENSE.md.

Sound is OFF by default. The research behind FRITS says background music helps
some people and distracts others, so Fritz switches it on himself.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable

SOUND_ON_BY_DEFAULT = False


@dataclass(frozen=True)
class Sound:
    name: str  # stable id, used in URLs and settings
    title: str  # shown to Fritz (German)
    description: str
    path: str  # relative to assets/sounds/, e.g. "cues/done.mp3"


CUES: dict[str, Sound] = {
    s.name: s
    for s in [
        Sound("done", "Erledigt!", "Ein kleiner Schritt ist geschafft.", "cues/done.mp3"),
        Sound(
            "sent",
            "Rechnung verschickt",
            "Eine Rechnung oder ein Angebot ist raus.",
            "cues/sent.mp3",
        ),
        Sound("shutdown", "Feierabend", "Der Arbeitstag ist abgeschlossen.", "cues/shutdown.mp3"),
        Sound("focus", "Fokus-Runde", "Ein Büro-Block beginnt.", "cues/focus.mp3"),
    ]
}

TRACKS: dict[str, Sound] = {
    s.name: s
    for s in [
        Sound("morgen", "Morgen", "Ruhiger Start in den Tag.", "music/morgen.mp3"),
        Sound(
            "buero-block",
            "Büro-Block",
            "Gleichmäßige Musik zum Konzentrieren.",
            "music/buero-block.mp3",
        ),
    ]
}


def sounds_dir() -> Traversable:
    """The folder with all sound files (mount this as a static folder in the web app)."""
    return resources.files("frits") / "assets" / "sounds"


def cue_file(name: str) -> Traversable:
    return sounds_dir() / CUES[name].path


def track_file(name: str) -> Traversable:
    return sounds_dir() / TRACKS[name].path
