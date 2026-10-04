"""Channel contracts and failure data that cannot contain provider messages or tokens."""

import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    account: str
    thread_key: str
    message_id: str
    sender_id: str
    text: str
    kind: str = "dm"
    human: bool = True
    chat_type: str = "private"
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutboundMessage:
    text: str


class Channel(Protocol):
    name: str

    async def start(self, deliver): ...
    async def stop(self): ...
    async def send(self, thread_key: str, message: OutboundMessage) -> str: ...
    async def typing(self, thread_key: str): ...
    async def readiness(self): ...


class ProviderError(Exception):
    def __init__(self, status=None, retry_after=None):
        super().__init__("The channel provider request failed.")
        self.status = status
        self.retry_after = retry_after


def safe_failure(error):
    result = {"error_class": re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__)[:80]}
    status = getattr(error, "status", None)
    if type(status) is int and 400 <= status <= 599:
        result["http_status"] = status
    logger.warning("Channel failure: %s", result)
    return result


class BotUrlFilter(logging.Filter):
    """httpx logs URLs; Telegram puts credentials in their path. Redact before handlers see them."""

    def filter(self, record):
        record.msg = re.sub(r"/bot[^/\s]+/", "/bot[redacted]/", record.getMessage())
        record.args = ()
        return True
