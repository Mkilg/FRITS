"""tools/third_party_licenses.py: the license file that ships in the Mac app."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "lizenzen", ROOT / "tools" / "third_party_licenses.py"
)
lizenzen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lizenzen)


def dist(site: Path, name: str, version: str, meta: str, licenses: dict[str, str]) -> None:
    info = site / f"{name}-{version}.dist-info"
    (info / "licenses").mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{meta}\n", encoding="utf-8"
    )
    for file_name, text in licenses.items():
        (info / "licenses" / file_name).write_text(text, encoding="utf-8")


def test_lists_every_library_once_with_its_license_text(tmp_path):
    arm, intel = tmp_path / "arm", tmp_path / "intel"
    for site in (arm, intel):
        dist(site, "fpdf2", "2.9.0", "License-Expression: LGPL-3.0-only", {"LICENSE": "LGPL text"})
        dist(site, "pi_heif", "1.4.0", "Classifier: License :: OSI Approved :: LGPLv3", {})
        dist(site, "frits", "1.3.0", "License-Expression: MIT", {})
    env = tmp_path / "tess"
    (env / "conda-meta").mkdir(parents=True)
    (env / "conda-meta" / "tesseract-5.5.1-h1.json").write_text(
        json.dumps({"name": "tesseract", "version": "5.5.1", "license": "Apache-2.0"})
    )
    pkgs = tmp_path / "pkgs"
    (pkgs / "tesseract-5.5.1-h1" / "info" / "licenses").mkdir(parents=True)
    (pkgs / "tesseract-5.5.1-h1" / "info" / "licenses" / "LICENSE").write_text("Apache text")

    out = tmp_path / "LIZENZEN.txt"
    args = [str(out), "--site", str(arm), "--site", str(intel)]
    assert lizenzen.main([*args, "--conda-env", str(env), "--conda-pkgs", str(pkgs)]) == 0
    text = out.read_text(encoding="utf-8")

    assert text.count("- fpdf2 2.9.0: LGPL-3.0-only") == 1
    assert "- pi_heif 1.4.0: LGPLv3" in text
    assert "LGPL text" in text
    assert "- tesseract 5.5.1: Apache-2.0" in text and "Apache text" in text
    assert "- frits " not in text  # FRITS itself is MIT, see LICENSE
    assert "Python Software Foundation" in text
