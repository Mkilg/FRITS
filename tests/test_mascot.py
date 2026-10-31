import xml.etree.ElementTree as ET

import pytest

from frits.mascot import MOODS, _lines, line, mascot_svg, step

SETUP_STEPS = [
    "welcome", "choose_ai", "store_key", "business_details", "tax_status",
    "redaction_test", "trick_document", "permissions", "first_invoice", "done",
]


@pytest.mark.parametrize("mood", MOODS)
def test_every_mood_is_valid_svg(mood):
    root = ET.fromstring(mascot_svg(mood))
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == "0 0 240 240"


def test_unknown_mood_is_rejected():
    with pytest.raises(ValueError):
        mascot_svg("angry")


@pytest.mark.parametrize("key", SETUP_STEPS)
def test_every_setup_step_has_copy(key):
    s = step(key)
    assert s["mood"] in MOODS
    assert s["title"] and s["text"]


def test_moments_use_known_moods():
    for key in _lines()["moments"]:
        assert line(key)["mood"] in MOODS
