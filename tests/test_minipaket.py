"""The mini package "Erst schwärzen, dann fragen" (minipaket/): stays in step with FRITS and
ships as a complete, self-contained folder. Its JavaScript has its own tests
(node --test minipaket/tests/*.test.cjs, and minipaket/tests/e2e.cjs in a real browser)."""

import importlib.util
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("minipaket", ROOT / "tools" / "minipaket.py")
minipaket = importlib.util.module_from_spec(spec)
sys.modules["minipaket"] = minipaket  # dataclasses look their module up there
spec.loader.exec_module(minipaket)


def test_prompts_and_pictures_are_current():
    # If this fails: uv run python tools/minipaket.py
    assert minipaket.auffrischen(nur_pruefen=True) == []


def test_every_local_link_and_picture_exists():
    for seite in ("index.html", "kurs.html", "werkbank.html", "prompts.html"):
        text = (minipaket.PAKET / seite).read_text(encoding="utf-8")
        for ziel in re.findall(r'(?:src|href)="(?![a-z]+:)([^"#]+)', text):
            assert (minipaket.PAKET / ziel).exists(), f"{seite}: {ziel}"
        # Prompt links on other pages point at prompts that exist
        for anker in re.findall(r'href="prompts\.html#(prompt-[\w-]+)"', text):
            assert f'id="{anker}"' in (minipaket.PAKET / "prompts.html").read_text(encoding="utf-8")


def test_every_page_forbids_network_connections():
    for seite in ("index.html", "kurs.html", "werkbank.html", "prompts.html"):
        text = (minipaket.PAKET / seite).read_text(encoding="utf-8")
        assert "connect-src 'none'" in text, seite
        # Nothing is loaded from elsewhere: no CDN, no web fonts
        assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)="https?:', text), seite


def test_no_third_party_names():
    # Marie's rule: her own name only, no friends' or colleagues' names, and no ARD claim
    for datei in minipaket.DATEIEN:
        text = (minipaket.PAKET / datei).read_text(encoding="utf-8")
        for name in ("Espenlaub", "Schmalzried", "Gregor", "ARD", "Fritz,"):
            assert name not in text, f"{datei}: {name}"


def test_zip_has_everything(tmp_path):
    ziel = tmp_path / "paket.zip"
    minipaket.zip_bauen(ziel)
    with zipfile.ZipFile(ziel) as z:
        namen = z.namelist()
    ordner = minipaket.ORDNER_IM_ZIP
    for datei in minipaket.DATEIEN + ("LIZENZ.txt",):
        assert f"{ordner}/{datei}" in namen
    for bild in minipaket.BILDER:
        assert f"{ordner}/bilder/{bild}" in namen
    prompts = [n for n in namen if n.startswith(f"{ordner}/Prompts/")]
    assert len(prompts) == sum(len(keys) for _, _, keys in minipaket.GRUPPEN)
    assert not any("/tests/" in n for n in namen)
    assert ziel.stat().st_size < 600_000
