"""Provider construction from configuration (DD-040).

**There is no fallback path in this module, by design.** If the configured
provider is unavailable, construction raises. Silently substituting another
provider could send email and file content to a third party the user did not
choose — which makes FR-214 a security requirement, not a usability one.
"""

from __future__ import annotations

from typing import Any

from panini.errors import ConfigError, ProviderUnavailable
from panini.llm.auth import build_auth
from panini.llm.provider import LLMProvider
from panini.llm.providers.anthropic import AnthropicProvider
from panini.llm.providers.ollama import OllamaProvider
from panini.llm.providers.openai_compatible import OpenAICompatibleProvider
from panini.security.credentials import CredentialStore

#: Defaults per known vendor. A user can point `base_url` anywhere, so these are
#: conveniences rather than a closed list.
PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "max_context": 128_000,
        "auth": {"type": "api_key", "api_key_handle": "openai_key"},
    },
    "google": {
        # Google's OpenAI-compatible surface; also accepts OAuth for user accounts.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "max_context": 1_000_000,
        "auth": {"type": "api_key", "api_key_handle": "google_key"},
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "max_context": 128_000,
        "auth": {"type": "api_key", "api_key_handle": "openrouter_key"},
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "max_context": 128_000,
        "auth": {"type": "api_key", "api_key_handle": "groq_key"},
    },
    "azure_openai": {
        # Entra ID issues short-lived tokens; the OAuth strategy refreshes them.
        "max_context": 128_000,
        "auth": {"type": "oauth"},
    },
    "anthropic": {
        "max_context": 200_000,
        "auth": {
            "type": "api_key",
            "api_key_handle": "anthropic_key",
            "header": "x-api-key",
            "scheme": "",
        },
    },
}


def build_provider(
    provider_name: str,
    provider_cfg: dict[str, Any],
    store: CredentialStore,
) -> LLMProvider:
    """Construct a provider. Does not verify reachability — see `get_provider`."""
    name = provider_name.lower()
    preset = PRESETS.get(name, {})
    merged = {**preset, **provider_cfg}

    model = merged.get("model")
    if not model:
        raise ConfigError(f"llm.{name}.model is required")

    if name == "ollama":
        return OllamaProvider(
            model=str(model),
            host=str(merged.get("host", "http://localhost:11434")),
            options=dict(merged.get("options", {})),
        )

    auth_spec = merged.get("auth") or preset.get("auth")
    auth = build_auth(store, auth_spec)

    if name == "anthropic":
        return AnthropicProvider(
            model=str(model),
            auth=auth,
            base_url=str(merged.get("base_url", "https://api.anthropic.com/v1")),
            max_context=int(merged.get("max_context", 200_000)),
        )

    base_url = merged.get("base_url")
    if not base_url:
        raise ConfigError(
            f"llm.{name}.base_url is required for provider {name!r}. "
            f"Known presets: {', '.join(sorted(PRESETS))}"
        )

    return OpenAICompatibleProvider(
        model=str(model),
        base_url=str(base_url),
        auth=auth,
        name=name,
        max_context=int(merged.get("max_context", 128_000)),
        is_local=bool(merged.get("is_local", False)),
        extra_body=dict(merged.get("extra_body", {})),
    )


async def get_provider(
    provider_name: str,
    provider_cfg: dict[str, Any],
    store: CredentialStore,
) -> LLMProvider:
    """Construct and verify the configured provider.

    Raises ProviderUnavailable rather than substituting a different provider
    (FR-214). The caller surfaces this to the user; nothing downstream may
    interpret it as permission to use a cloud model instead.
    """
    provider = build_provider(provider_name, provider_cfg, store)
    status = await provider.health()
    if not status.available:
        await provider.aclose()
        raise ProviderUnavailable(provider.name, status.detail)
    return provider
