from __future__ import annotations

import json

import anthropic
from pydantic import ValidationError

from frits.providers import ProviderError, Usage, explain_status
from frits.schema import JSON_SCHEMA, SYSTEM_PROMPT, ExtractionResult, user_message


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str,
        retry_model: str | None = None,
        chat_model: str | None = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.retry_model = retry_model
        self.chat_model = chat_model or retry_model or model

    def chat(self, system: str, messages: list[dict]) -> tuple[str, Usage]:
        """One answer in the Notfall-Chat. `messages` are already redacted; Claude gets no tools,
        so it can only answer: it can't open, send or store anything."""
        request = {
            "model": self.chat_model,
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
            "output_config": {"effort": "medium"},
        }
        try:
            try:
                # If a safety check declines, the API answers with a suitable fallback model.
                response = self.client.beta.messages.create(
                    **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            except anthropic.BadRequestError:
                response = self.client.messages.create(**request)
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Keine Verbindung zu Claude. Ist das Internet an?") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                explain_status("Claude", exc.status_code, _detail(exc), self.chat_model)
            ) from exc

        usage = Usage(
            "claude", self.chat_model, response.usage.input_tokens, response.usage.output_tokens
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "refusal":
            raise ProviderError(
                "Claude möchte auf diese Frage nicht antworten. Formulier sie bitte anders."
            )
        if response.stop_reason == "max_tokens":
            text += "\n\n(Die Antwort war zu lang und wurde abgeschnitten.)"
        if not text:
            raise ProviderError("Claude hat keine Antwort geschickt. Versuch es noch einmal.")
        return text, usage

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
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Keine Verbindung zu Claude. Ist das Internet an?") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                explain_status("Claude", exc.status_code, _detail(exc), model)
            ) from exc

        usage = Usage("claude", model, response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"Claude hat die Antwort nicht beendet ({response.stop_reason}).")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return ExtractionResult.model_validate(json.loads(text)), usage
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError("Claude hat keine gültige Antwort geliefert.") from exc


def _detail(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return ""
