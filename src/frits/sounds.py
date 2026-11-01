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
        Sound(
            "schwung",
            "Schwung",
            "Leichter Groove mit sanftem Beat, wenn es vorangehen soll.",
            "music/schwung.mp3",
        ),
        Sound(
            "abenteuer",
            "Abenteuer",
            "Trommeln, Harfe und Horn für die Steuer-Quest.",
            "music/abenteuer.mp3",
        ),
    ]
}


@dataclass(frozen=True)
class Mood:
    name: str  # stored in settings.toml as `music`
    title: str
    description: str
    track: str  # a key of TRACKS


# Fritz picks the mood in Einstellungen; the ♪ Musik button plays that track.
MOODS: dict[str, Mood] = {
    m.name: m
    for m in [
        Mood(
            "entspannt",
            "Entspannt",
            "Ruhige Flächen ohne Beat. Gut zum Konzentrieren.",
            "buero-block",
        ),
        Mood("sanft", "Sanft", "Wie ein ruhiger Morgen, noch etwas heller.", "morgen"),
        Mood("motivierend", "Motivierend", "Leichter Groove, wenn ein Stapel weg muss.", "schwung"),
        Mood(
            "abenteuerlich", "Abenteuerlich", "Trommeln und Horn, wie auf einer Reise.", "abenteuer"
        ),
    ]
}
DEFAULT_MOOD = "entspannt"
# The Steuer-Quest is an adventure, so it always has its own music.
QUEST_TRACK = "abenteuer"


def track_for(mood: str, path: str = "/") -> str:
    """Which track the ♪ Musik button plays on the page at `path`."""
    if path.startswith("/steuer"):
        return QUEST_TRACK
    return MOODS.get(mood, MOODS[DEFAULT_MOOD]).track


def sounds_dir() -> Traversable:
    """The folder with all sound files (mount this as a static folder in the web app)."""
    return resources.files("frits") / "assets" / "sounds"


def cue_file(name: str) -> Traversable:
    return sounds_dir() / CUES[name].path


def track_file(name: str) -> Traversable:
    return sounds_dir() / TRACKS[name].path
