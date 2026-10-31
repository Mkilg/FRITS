import os
import shutil
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = sorted((FIXTURES / "docs").iterdir())


def answer(doc: Path) -> dict:
    return yaml.safe_load((FIXTURES / "expected" / f"{doc.stem}.yaml").read_text(encoding="utf-8"))


def _require(env: str, available: bool, what: str) -> None:
    # Locally a missing tool skips its tests; CI sets the env var so a missing tool fails instead.
    if not available:
        if os.environ.get(env):
            pytest.fail(f"{what} is required in this environment but not installed")
        pytest.skip(f"{what} not installed")


@pytest.fixture
def needs_ocr():
    _require("FRITS_REQUIRE_OCR", shutil.which("tesseract") is not None, "Tesseract")


@pytest.fixture(scope="session")
def nlp():
    from frits.redact import load_ner

    try:
        model = load_ner()
    except RuntimeError:
        model = None
    _require("FRITS_REQUIRE_NER", model is not None, "German spaCy model")
    return model


@pytest.fixture(scope="session")
def customers():
    from frits.customers import load_customers

    return load_customers(FIXTURES / "customers")
