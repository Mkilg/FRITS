from __future__ import annotations

import json
import os

import openai
from pydantic import ValidationError

from frits.providers import ProviderError, Usage
from frits.schema import JSON_SCHEMA, SYSTEM_PROMPT, ExtractionResult, user_message

BASE_URLS = {
    "openai": None,
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
}


class OpenAICompatProvider:
    def __init__(self, name: str, api_key: str, model: str):
        self.name = name
        self.model = model
        self.client = openai.OpenAI(api_key=api_key, base_url=BASE_URLS[name])

    def extract(self, redacted_text: str) -> tuple[ExtractionResult, Usage]:
        last_error: ProviderError | None = None
        for _ in range(2):  # one retry on an unusable answer
            try:
                return self._call(redacted_text)
            except ProviderError as exc:
                last_error = exc
        raise last_error

    def _call(self, redacted_text: str) -> tuple[ExtractionResult, Usage]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message(redacted_text)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "extraction", "strict": True, "schema": JSON_SCHEMA},
                },
            )
        except openai.AuthenticationError as exc:
            raise ProviderError(f"Der API-Schlüssel für {self.name} wurde abgelehnt.") from exc
        except openai.APIConnectionError as exc:
            where = (
                "zum lokalen Modell (läuft Ollama?)" if self.name == "ollama" else "zum KI-Dienst"
            )
            raise ProviderError(f"Keine Verbindung {where}.") from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.name} hat mit Fehler {exc.status_code} geantwortet."
            ) from exc

        tokens = response.usage
        usage = Usage(
            self.name,
            self.model,
            tokens.prompt_tokens if tokens else 0,
            tokens.completion_tokens if tokens else 0,
        )
        text = response.choices[0].message.content or ""
        try:
            return ExtractionResult.model_validate(json.loads(text)), usage
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(f"{self.name} hat keine gültige Antwort geliefert.") from exc
