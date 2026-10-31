from __future__ import annotations

import json

import anthropic
from pydantic import ValidationError

from frits.providers import ProviderError, Usage
from frits.schema import JSON_SCHEMA, SYSTEM_PROMPT, ExtractionResult, user_message


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str, retry_model: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.retry_model = retry_model

    def extract(self, redacted_text: str) -> tuple[ExtractionResult, Usage]:
        try:
            return self._call(self.model, redacted_text)
        except ProviderError:
            # One retry on the stronger model when the answer was unusable.
            if not self.retry_model or self.retry_model == self.model:
                raise
            return self._call(self.retry_model, redacted_text)

    def _call(self, model: str, redacted_text: str) -> tuple[ExtractionResult, Usage]:
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message(redacted_text)}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": JSON_SCHEMA},
                },
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Der Claude-API-Schlüssel wurde abgelehnt.") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                "Claude ist gerade ausgelastet. Bitte später erneut versuchen."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Keine Verbindung zu Claude. Ist das Internet an?") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude hat mit Fehler {exc.status_code} geantwortet.") from exc

        usage = Usage("claude", model, response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"Claude hat die Antwort nicht beendet ({response.stop_reason}).")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return ExtractionResult.model_validate(json.loads(text)), usage
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError("Claude hat keine gültige Antwort geliefert.") from exc
