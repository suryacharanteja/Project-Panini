"""Stable user-facing provider errors without echoing upstream response bodies."""

import logging

from . import config

logger = logging.getLogger(__name__)


def provider_error(provider: str, error: Exception, key_name: str) -> str:
    logger.warning("%s request failed (%s)", provider, type(error).__name__)
    if not config.configured(key_name):
        return f"{key_name} is not configured"
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        return f"{provider} rejected its credential. Check the configured key and access."
    if status == 429:
        return f"{provider} is rate limited. Please retry shortly."
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return f"{provider} timed out. Please retry."
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return f"{provider} returned an invalid response. Please retry."
    return f"{provider} request failed. Check service configuration and try again."
