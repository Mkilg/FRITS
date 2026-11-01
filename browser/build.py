"""Build the FRITS browser version into browser/dist/ (a folder of plain files).

    uv run python browser/build.py                 # downloads what it needs
    uv run python browser/build.py --serve         # build, then open http://localhost:8800/

Everything the page loads ends up in dist/: Pyodide (Python for the browser), the Python
wheels, FRITS itself, Tesseract and the model runtime. Nothing is loaded from a CDN at run time.
The one exception is the redaction model (about 180 MB, too big for GitHub Pages): it comes from
Hugging Face once and is then kept by the browser.

Inputs are pinned: the Pyodide release below, the versions in uv.lock, browser/package-lock.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = HERE / "dist"
CACHE = HERE / ".cache"

PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = (
    f"https://github.com/pyodide/pyodide/releases/download/{PYODIDE_VERSION}/"
    f"pyodide-{PYODIDE_VERSION}.tar.bz2"
)
# Compiled packages come from the Pyodide release (built for WebAssembly).
PYODIDE_PACKAGES = [
    "micropip",
    "pydantic",
    "pillow",
    "pyyaml",
    "lxml",
    "jinja2",
    "markupsafe",
    "jiter",
    "pycryptodome",
    "certifi",
    "fonttools",
    "tomli-w",
    "requests",  # factur-x imports it (only used for an online check FRITS never calls)
]
# Pure-Python packages come from PyPI in the exact version of uv.lock.
PYPI_PACKAGES = [
    "annotated-doc",
    "annotated-types",
    "anthropic",
    "anyio",
    "defusedxml",
    "docstring-parser",
    "factur-x",
    "fastapi",
    "fpdf2",
    "h11",
    "httpcore2",
    "httpx2",
    "httpx2-jsfetch",
    "idna",
    "iso4217",
    "openai",
    "opentelemetry-api",
    "pypdf",
    "python-docx",
    "python-multipart",
    "python-stdnum",
    "pyzipper",
    "sniffio",
    "starlette",
    "tqdm",
    "truststore",
    "typing-extensions",
    "typing-inspection",
]
# Pinned where uv.lock has no entry (dependencies of fpdf2 that FRITS doesn't lock itself).
EXTRA_VERSIONS = {"defusedxml": "0.7.1"}
PYODIDE_CORE = [
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "pyodide.mjs",
    "pyodide.js",
    "pyodide-lock.json",
    "python_stdlib.zip",
]
NODE = HERE / "node_modules"
VENDOR = {
    "tesseract/tesseract.esm.min.js": "tesseract.js/dist/tesseract.esm.min.js",
    "tesseract/worker.min.js": "tesseract.js/dist/worker.min.js",
    "tesseract/core/tesseract-core-lstm.wasm.js": "tesseract.js-core/tesseract-core-lstm.wasm.js",
    "tesseract/core/tesseract-core-simd-lstm.wasm.js": (
        "tesseract.js-core/tesseract-core-simd-lstm.wasm.js"
    ),
    "tesseract/lang/deu.traineddata.gz": "@tesseract.js-data/deu/4.0.0/deu.traineddata.gz",
    "tesseract/lang/eng.traineddata.gz": "@tesseract.js-data/eng/4.0.0/eng.traineddata.gz",
    "transformers/transformers.web.min.js": "@huggingface/transformers/dist/transformers.web.min.js",
    "ort/ort-wasm-simd-threaded.jsep.mjs": "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs",
    "ort/ort-wasm-simd-threaded.jsep.wasm": "onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm",
}
SHELL_FILES = ["index.html", "shell.css", "shell.js", "sw.js", "py-worker.js", "helper-worker.js"]


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def pyodide_dir(local: Path | None) -> Path:
    if local:
        return local
    target = CACHE / f"pyodide-{PYODIDE_VERSION}"
    if not (target / "pyodide-lock.json").exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        archive = CACHE / f"pyodide-{PYODIDE_VERSION}.tar.bz2"
        if not archive.exists():
            print("Lade Pyodide", PYODIDE_VERSION, "(einmalig, ca. 340 MB) ...")
            urllib.request.urlretrieve(PYODIDE_URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(CACHE / "unpack", filter="data")
        shutil.move(CACHE / "unpack" / "pyodide", target)
        shutil.rmtree(CACHE / "unpack")
        archive.unlink()
    return target


def pyodide_files(src: Path) -> list[str]:
    """The package files for PYODIDE_PACKAGES and everything they depend on."""
    lock = json.loads((src / "pyodide-lock.json").read_text())["packages"]
    todo, seen = list(PYODIDE_PACKAGES), set()
    while todo:
        name = todo.pop().lower().replace("_", "-")
        if name in seen:
            continue
        seen.add(name)
        todo.extend(lock[name]["depends"])
    return sorted(lock[n]["file_name"] for n in seen), sorted(seen)


def locked_versions() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return {p["name"]: p["version"] for p in lock["package"]}


def pypi_wheels(dest: Path) -> None:
    versions = {**EXTRA_VERSIONS, **locked_versions()}
    specs = [f"{name}=={versions[name]}" for name in PYPI_PACKAGES]
    run(
        ["uvx", "--from", "pip", "pip", "download", "--no-deps", "--only-binary=:all:", "-q",
         "--python-version", "3.14", "-d", str(dest), *specs]
    )


def build(local_pyodide: Path | None) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "pyodide").mkdir(parents=True)
    wheels = DIST / "wheels"
    wheels.mkdir()

    src = pyodide_dir(local_pyodide)
    files, names = pyodide_files(src)
    for name in PYODIDE_CORE + files:
        shutil.copy2(src / name, DIST / "pyodide" / name)

    pypi_wheels(wheels)
    run(["uv", "build", "--wheel", "-q", "-o", str(wheels)], cwd=ROOT)

    if not NODE.exists():
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=HERE)
    for target, source in VENDOR.items():
        out = DIST / "vendor" / target
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(NODE / source, out)

    shutil.copytree(ROOT / "src/frits/web/static", DIST / "static")
    for name in SHELL_FILES:
        shutil.copy2(HERE / name, DIST / name)
    shutil.copy2(ROOT / "src/frits/web/static/fry.svg", DIST / "fry.svg")

    wheel_names = sorted(p.name for p in wheels.glob("*.whl"))
    digest = hashlib.sha256()
    for p in sorted(DIST.rglob("*")):
        if p.is_file():
            digest.update(p.name.encode())
            digest.update(p.read_bytes())
    manifest = {
        "version": digest.hexdigest()[:12],
        "pyodide": PYODIDE_VERSION,
        "pyodidePackages": names,
        "wheels": wheel_names,
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (DIST / ".nojekyll").write_text("")  # GitHub Pages: serve files as they are
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1e6
    print(f"Fertig: {DIST} ({size:.0f} MB, Version {manifest['version']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pyodide-dir", type=Path, help="an unpacked Pyodide release folder")
    parser.add_argument("--serve", action="store_true", help="serve dist/ on localhost:8800")
    parser.add_argument("--no-build", action="store_true", help="only serve")
    args = parser.parse_args()
    if not args.no_build:
        build(args.pyodide_dir)
    if args.serve:
        sys.path.insert(0, str(HERE))
        from serve import serve

        serve(DIST, 8800)


if __name__ == "__main__":
    main()
