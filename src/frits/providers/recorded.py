"""A provider that replays a stored answer. Used by tests and CI, so they need no API key."""

from __future__ import annotations

import json
from pathlib import Path

from frits.providers import Usage
from frits.schema import ExtractionResult


class RecordedProvider:
    name = "recorded"

    def __init__(self, result: ExtractionResult):
        self.result = result
        self.calls: list[str] = []

    @classmethod
    def from_file(cls, path: Path) -> RecordedProvider:
        return cls(ExtractionResult.model_validate(json.loads(Path(path).read_text("utf-8"))))

    def extract(self, redacted_text: str) -> tuple[ExtractionResult, Usage]:
        self.calls.append(redacted_text)
        return self.result, Usage("recorded", "recorded", len(redacted_text) // 4, 200)
