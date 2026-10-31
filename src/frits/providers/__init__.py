"""AI providers. Each one turns redacted text into an ExtractionResult and reports its usage.

Two adapters cover all four choices: the Anthropic SDK for Claude, and one OpenAI-compatible
client for OpenAI, OpenRouter and a local Ollama server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from frits.schema import ExtractionResult

# Defaults Fritz can change in settings.toml under [models].
DEFAULT_MODELS = {
    "claude": "claude-haiku-5-5",
    "claude_retry": "claude-sonnet-5-5",
    "openai": "gpt-5-mini",
    "openrouter": "anthropic/claude-haiku-5.5",
    "ollama": "qwen3:8b",
}

# USD per million tokens (input, output), for the cost line in the audit log.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}

KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
KEYCHAIN_SERVICE = "FRITS"


class ProviderError(Exception):
    """The AI call failed or returned something unusable. Message is shown to the user."""


@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float | None:
        price = PRICES.get(self.model)
        if price is None:
            return None
        return (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1_000_000


class Provider(Protocol):
    name: str

    def extract(self, redacted_text: str) -> tuple[ExtractionResult, Usage]: ...


def get_api_key(provider: str) -> str | None:
    """Environment first (development), then the macOS Keychain (Fritz's computer)."""
    env = KEY_ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env]
    import keyring
    from keyring.errors import KeyringError

    try:
        return keyring.get_password(KEYCHAIN_SERVICE, provider)
    except KeyringError:  # no keychain backend (e.g. Docker): treat as "no key stored"
        return None


def store_api_key(provider: str, key: str) -> None:
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, provider, key)


def make_provider(name: str, models: dict[str, str] | None = None) -> Provider:
    models = {**DEFAULT_MODELS, **(models or {})}
    if name == "claude":
        from frits.providers.anthropic_provider import ClaudeProvider

        return ClaudeProvider(_require_key(name), models["claude"], models["claude_retry"])
    if name in ("openai", "openrouter", "ollama"):
        from frits.providers.openai_compat import OpenAICompatProvider

        key = "ollama" if name == "ollama" else _require_key(name)
        return OpenAICompatProvider(name, key, models[name])
    raise ProviderError(f"Unbekannter KI-Anbieter: {name}")


def _require_key(name: str) -> str:
    key = get_api_key(name)
    if not key:
        raise ProviderError(
            f"Für {name} ist noch kein API-Schlüssel hinterlegt. Bitte im Einrichtungsassistenten eintragen."
        )
    return key
