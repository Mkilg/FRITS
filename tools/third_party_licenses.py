"""Write the license notices for everything the Mac app ships besides FRITS itself.

Most libraries FRITS uses are MIT, BSD or Apache; a few are LGPL. All of them ask that their
license text travels with the program. This script collects them into one plain text file:

    python3 tools/third_party_licenses.py OUT.txt --site DIR [--site DIR ...]
        [--conda-env DIR ...] [--conda-pkgs DIR]

--site        a site-packages folder: every *.dist-info in it is listed with its license files
--conda-env   a conda/micromamba environment (Tesseract and its libraries): conda-meta/*.json
--conda-pkgs  the package cache of those environments, where the license texts live

Only the standard library is used, so the build can run it with any Python 3.
"""

from __future__ import annotations

import argparse
import json
from email.parser import Parser
from pathlib import Path

LINE = "=" * 78

HEADER = """\
FRITS: Lizenzen der mitgelieferten Programme und Bibliotheken
(Third-party licenses of the programs and libraries shipped with FRITS)

FRITS selbst steht unter der MIT-Lizenz (siehe LICENSE im Quellcode:
https://github.com/Mkilg/FRITS). In der App stecken außerdem die folgenden
Programme und Bibliotheken anderer Leute. Sie alle sind freie Software; hier
stehen ihre Namen, Versionen, Lizenzen und Lizenztexte.

Bibliotheken unter LGPL (zum Beispiel fpdf2, pi-heif mit libheif und
libde265) liegen als eigene, austauschbare Dateien in
FRITS.app/Contents/Resources/runtime/<chip>/lib/python*/site-packages.
Ihren Quellcode gibt es auf https://pypi.org bzw. den unten genannten Seiten.
"""

PYTHON_NOTE = """\
Python (CPython) und die mitgelieferten Bibliotheken
----------------------------------------------------
Lizenz: Python Software Foundation License (PSF-2.0), siehe
https://docs.python.org/3/license.html
Gebaut von python-build-standalone (https://github.com/astral-sh/python-build-standalone).
Darin enthalten sind unter anderem OpenSSL (Apache-2.0), SQLite (gemeinfrei),
libffi (MIT), xz/liblzma (0BSD), zlib (Zlib), bzip2 (bzip2-1.0.6),
mpdecimal (BSD-2-Clause), libedit (BSD-3-Clause) und Tcl/Tk (TCL).
Die vollständigen Texte stehen bei python-build-standalone.
"""

TESSDATA_NOTE = """\
Tesseract-Sprachdaten (deu, eng, osd)
-------------------------------------
Lizenz: Apache-2.0, https://github.com/tesseract-ocr/tessdata
"""

LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _license_of(meta) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) < 100:
        return text
    return "siehe Lizenztext"


def python_packages(sites: list[Path]) -> list[dict]:
    """Every distribution in the given site-packages folders, once per name."""
    found: dict[str, dict] = {}
    for site in sites:
        for info in sorted(site.glob("*.dist-info")):
            metadata = info / "METADATA"
            if not metadata.is_file():
                continue
            meta = Parser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"))
            name = meta.get("Name") or info.name.split("-")[0]
            key = name.lower().replace("_", "-")
            if key == "frits" or key in found:
                continue
            files = sorted(p for p in (info / "licenses").rglob("*") if p.is_file())
            files += sorted(
                p
                for p in info.iterdir()
                if p.is_file() and p.name.upper().startswith(LICENSE_NAMES)
            )
            found[key] = {
                "name": name,
                "version": meta.get("Version", "?"),
                "license": _license_of(meta),
                "home": meta.get("Home-page") or _project_url(meta),
                "texts": [(p.name, _read(p)) for p in files],
            }
    return sorted(found.values(), key=lambda p: p["name"].lower())


def _project_url(meta) -> str:
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in {"homepage", "source", "repository", "source code"}:
            return url.strip()
    return ""


def conda_packages(envs: list[Path], pkgs: Path | None) -> list[dict]:
    found: dict[str, dict] = {}
    for env in envs:
        for record in sorted((env / "conda-meta").glob("*.json")):
            data = json.loads(record.read_text(encoding="utf-8"))
            name = data.get("name", record.stem)
            if name in found:
                continue
            texts = []
            if pkgs is not None:
                folder = pkgs / record.stem / "info" / "licenses"
                if folder.is_dir():
                    texts = [
                        (p.relative_to(folder).as_posix(), _read(p))
                        for p in sorted(folder.rglob("*"))
                        if p.is_file()
                    ]
            found[name] = {
                "name": name,
                "version": data.get("version", "?"),
                "license": data.get("license") or "siehe Lizenztext",
                "home": f"https://anaconda.org/conda-forge/{name}",
                "texts": texts,
            }
    return sorted(found.values(), key=lambda p: p["name"].lower())


def render(python: list[dict], conda: list[dict], with_python_note: bool = True) -> str:
    parts = [HEADER]
    if with_python_note:
        parts += [LINE, PYTHON_NOTE]
    sections = [("Python-Bibliotheken", python)]
    if conda:
        sections.append(("Texterkennung: Tesseract und seine Bibliotheken (conda-forge)", conda))
    for title, packages in sections:
        parts += [LINE, title.upper(), LINE, ""]
        parts += [f"- {p['name']} {p['version']}: {p['license']}" for p in packages]
        parts.append("")
        for p in packages:
            parts += [LINE, f"{p['name']} {p['version']}", f"Lizenz: {p['license']}"]
            if p["home"]:
                parts.append(f"Quelle: {p['home']}")
            for file_name, text in p["texts"]:
                parts += ["", f"--- {file_name} ---", text]
            parts.append("")
    if conda:
        parts += [LINE, TESSDATA_NOTE]
    return "\n".join(parts).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--site", type=Path, action="append", default=[])
    parser.add_argument("--conda-env", type=Path, action="append", default=[])
    parser.add_argument("--conda-pkgs", type=Path)
    args = parser.parse_args(argv)
    python = python_packages(args.site)
    conda = conda_packages(args.conda_env, args.conda_pkgs)
    args.out.write_text(render(python, conda), encoding="utf-8")
    print(f"{args.out}: {len(python)} Python-Pakete, {len(conda)} weitere Pakete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
