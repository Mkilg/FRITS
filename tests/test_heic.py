"""iPhone photos (HEIC): FRITS reads them with pi-heif.

pi-heif only decodes and ships under LGPL. Its sibling pillow-heif bundles the GPL x265 encoder in
its ready-made packages, which would end up inside the Mac app and clash with FRITS's MIT license.
"""

import tomllib
from pathlib import Path

from PIL import Image

from frits.ingest import extract_text

ROOT = Path(__file__).resolve().parents[1]
HEIC = Path(__file__).parent / "fixtures" / "heic" / "beleg.heic"


def test_heic_photo_opens():
    from pi_heif import register_heif_opener

    register_heif_opener()
    with Image.open(HEIC) as img:
        assert img.size == (900, 260)


def test_heic_receipt_is_read(needs_ocr):
    source = extract_text(HEIC)
    assert source.ocr_used
    assert "Summe" in source.text


def test_no_gpl_heif_package():
    """pillow-heif's binary wheels are GPLv2 (x265). Keep it out of FRITS and the Mac app."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    names = {p["name"] for p in lock["package"]}
    assert "pi-heif" in names
    assert "pillow-heif" not in names
