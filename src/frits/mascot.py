"""FRITS the fry: mascot pictures (SVG) and the German lines it says.

The files live in src/frits/assets/mascot/ so they ship inside the package.
"""

import json
from functools import cache
from importlib.resources import files

MOODS = ("welcome", "thinking", "done", "warning", "feierabend")

_ASSETS = files("frits") / "assets" / "mascot"


def mascot_svg(mood: str) -> str:
    """Return the SVG markup for one mood, ready to inline into a web page."""
    if mood not in MOODS:
        raise ValueError(f"Unknown mascot mood {mood!r}. Choose one of: {', '.join(MOODS)}")
    return (_ASSETS / f"{mood}.svg").read_text(encoding="utf-8")


@cache
def _lines() -> dict:
    return json.loads((_ASSETS / "lines.json").read_text(encoding="utf-8"))


def step(key: str) -> dict:
    """Setup assistant step: {"mood", "title", "text"}."""
    return _lines()["steps"][key]


def line(key: str) -> dict:
    """A setup step or an everyday moment (e.g. "draft_ready"): {"mood", "text", ...}."""
    data = _lines()
    if key in data["steps"]:
        return data["steps"][key]
    return data["moments"][key]
