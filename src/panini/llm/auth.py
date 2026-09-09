"""Authentication strategies for LLM providers.

Three shapes exist in practice and the provider code should not care which is in
use:

* **None** — a local runtime such as Ollama, reachable without credentials.
* **API key** — a long-lived secret sent as a header (OpenAI, Anthropic, Google
  AI Studio, most self-hosted gateways).
* **OAuth** — a short-lived access token refreshed from a stored refresh token
  (Google via a user account, Azure OpenAI via Entra ID, enterprise gateways).

Every strategy resolves secrets through a credential *handle*. Values are read
from the encrypted store at request time and never enter model context, logs,
the audit trail, or an exception message (DD-115, FR-542).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from panini.errors import CredentialError, ProviderError
from panini.security.credentials import CredentialStore

#: Refresh this many seconds before nominal expiry, so a token cannot lapse
#: between the check and the request reaching the server.
REFRESH_SKEW_S = 120


class AuthStrategy(ABC):
    """Supplies the headers a provider needs for one request."""

    @abstractmethod
    async def headers(self) -> dict[str, str]: ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this strategy can produce credentials right now.

        Drives FR-426: an unconfigured provider is reported at startup rather
        than failing mid-conversation.
        """

    @property
    def describe(self) -> str:
        """Human-readable label for the UI. Never includes a secret."""
        return type(self).__name__


class NoAuth(AuthStrategy):
    """For local runtimes that need no credentials."""

    async def headers(self) -> dict[str, str]:
        return {}

    def is_configured(self) -> bool:
        return True

    @property
    def describe(self) -> str:
        return "no authentication (local)"


class ApiKeyAuth(AuthStrategy):
    """Long-lived key sent in a header.

    The header name and scheme vary by vendor, so both are parameters rather
    than hard-coded — Anthropic uses ``x-api-key``, OpenAI uses
    ``Authorization: Bearer``.
    """

    def __init__(
        self,
        store: CredentialStore,
        handle: str,
        *,
        header: str = "Authorization",
        scheme: str = "Bearer",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._handle = handle
        self._header = header
        self._scheme = scheme
        self._extra = extra_headers or {}

    async def headers(self) -> dict[str, str]:
        key = self._store.resolve((self._handle,)).get(self._handle)
        value = f"{self._scheme} {key}" if self._scheme else key
        return {self._header: value, **self._extra}

    def is_configured(self) -> bool:
        return self._store.has(self._handle)

    @property
    def describe(self) -> str:
        return f"API key ({self._handle})"


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """Endpoints and identifiers for an OAuth 2.0 provider.

    ``client_secret_handle`` is optional: desktop applications use PKCE and are
    public clients, so many providers issue no secret at all.
    """

    token_url: str
    client_id: str
    scopes: tuple[str, ...] = ()
    client_secret_handle: str | None = None
    audience: str | None = None
    extra_token_params: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _AccessToken:
    value: str
    expires_at: float

    @property
    def is_fresh(self) -> bool:
        return time.time() < self.expires_at - REFRESH_SKEW_S


class OAuthAuth(AuthStrategy):
    """Bearer token obtained by refreshing a stored refresh token.

    The access token is held in memory only. The refresh token lives in the
    encrypted store and is never returned to callers — a caller that could read
    it could also persist it somewhere unencrypted.
    """

    def __init__(
        self,
        store: CredentialStore,
        refresh_token_handle: str,
        config: OAuthConfig,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._store = store
        self._handle = refresh_token_handle
        self._config = config
        self._http = http
        self._token: _AccessToken | None = None

    async def headers(self) -> dict[str, str]:
        token = await self._access_token()
        return {"Authorization": f"Bearer {token}"}

    def is_configured(self) -> bool:
        return self._store.has(self._handle)

    async def _access_token(self) -> str:
        if self._token and self._token.is_fresh:
            return self._token.value
        await self._refresh()
        assert self._token is not None
        return self._token.value

    async def _refresh(self) -> None:
        try:
            refresh_token = self._store.resolve((self._handle,)).get(self._handle)
        except CredentialError as exc:
            raise ProviderError(
                f"OAuth refresh token {self._handle!r} is not configured. "
                "Re-authenticate this provider."
            ) from exc

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._config.client_id,
            **self._config.extra_token_params,
        }
        if self._config.scopes:
            data["scope"] = " ".join(self._config.scopes)
        if self._config.audience:
            data["audience"] = self._config.audience
        if self._config.client_secret_handle:
            data["client_secret"] = self._store.resolve(
                (self._config.client_secret_handle,)
            ).get(self._config.client_secret_handle)

        client = self._http or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(self._config.token_url, data=data)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OAuth token endpoint unreachable: {exc}") from exc
        finally:
            if self._http is None:
                await client.aclose()

        if response.status_code >= 400:
            # Deliberately does not echo the response body: token endpoints
            # sometimes reflect the submitted credential back in an error.
            raise ProviderError(
                f"OAuth refresh failed with HTTP {response.status_code}. "
                "The stored refresh token may have been revoked; re-authenticate."
            )

        payload: dict[str, Any] = response.json()
        access = payload.get("access_token")
        if not access:
            raise ProviderError("OAuth response contained no access_token")

        self._token = _AccessToken(
            value=access,
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
        )

        # Providers may rotate the refresh token; persist the new one or the
        # next refresh fails with an already-consumed grant.
        if (rotated := payload.get("refresh_token")) and rotated != refresh_token:
            self._store.set(self._handle, rotated)

    @property
    def describe(self) -> str:
        return f"OAuth ({self._handle})"


def build_auth(
    store: CredentialStore,
    spec: dict[str, Any] | None,
) -> AuthStrategy:
    """Construct an auth strategy from a provider's config block.

    ``spec`` is the ``auth:`` mapping in ``panini.yaml``; absent or ``type: none``
    yields NoAuth.
    """
    if not spec:
        return NoAuth()

    kind = str(spec.get("type", "none")).lower()

    if kind == "none":
        return NoAuth()

    if kind == "api_key":
        handle = spec.get("api_key_handle")
        if not handle:
            raise ProviderError("auth.type=api_key requires api_key_handle")
        return ApiKeyAuth(
            store,
            str(handle),
            header=str(spec.get("header", "Authorization")),
            scheme=str(spec.get("scheme", "Bearer")),
            extra_headers=dict(spec.get("extra_headers", {})),
        )

    if kind == "oauth":
        handle = spec.get("refresh_token_handle")
        if not handle:
            raise ProviderError("auth.type=oauth requires refresh_token_handle")
        return OAuthAuth(
            store,
            str(handle),
            OAuthConfig(
                token_url=str(spec["token_url"]),
                client_id=str(spec["client_id"]),
                scopes=tuple(spec.get("scopes", ())),
                client_secret_handle=spec.get("client_secret_handle"),
                audience=spec.get("audience"),
                extra_token_params=dict(spec.get("extra_token_params", {})),
            ),
        )

    raise ProviderError(f"unknown auth type {kind!r}; expected none, api_key, or oauth")
