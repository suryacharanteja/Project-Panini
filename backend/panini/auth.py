"""Opaque, boot-scoped roles. Observer and transport tokens cannot control sessions."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from .contracts import Fault


@dataclass(frozen=True)
class Principal:
    client_id: str
    role: str
    expires: float
    session_id: str | None = None
    share_captions: bool = False


class Credentials:
    def __init__(self, settings, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.tokens = {}
        self.tickets = {}

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def prune(self):
        now = self.clock()
        self.tokens = {k: p for k, p in self.tokens.items() if p.expires > now}
        self.tickets = {k: v for k, v in self.tickets.items() if v[0] > now}

    def bootstrap(self, token):
        if not token or not secrets.compare_digest(token, self.settings.bootstrap_token):
            raise Fault("UNAUTHORIZED", "Runtime authentication failed.", 401, "restart_panini")

    def issue(self, role, session_id=None, share_captions=False, ttl=3600, client_id=None):
        self.prune()
        if len(self.tokens) >= 32:
            raise Fault("CLIENT_LIMIT", "Too many runtime clients.", 429)
        token = secrets.token_urlsafe(32)
        principal = Principal(client_id or str(uuid4()), role, self.clock() + ttl, session_id, share_captions)
        self.tokens[self.key(token)] = principal
        return token, principal

    def resolve(self, token, instance_id=None):
        if instance_id is not None and instance_id != self.settings.instance_id:
            raise Fault("INSTANCE_CHANGED", "This runtime has restarted.", 401, "reconnect")
        principal = self.tokens.get(self.key(token or ""))
        if not principal or principal.expires <= self.clock():
            raise Fault("UNAUTHORIZED", "Your connection has expired.", 401, "reconnect")
        return principal

    def revoke(self, client_id):
        self.tokens = {k: p for k, p in self.tokens.items() if p.client_id != client_id}

    def ticket(self, role, session_id, share_captions=False, ttl=30):
        self.prune()
        if len(self.tickets) >= 16:
            raise Fault("TICKET_LIMIT", "Too many pending connections.", 429)
        ticket = secrets.token_urlsafe(32)
        self.tickets[self.key(ticket)] = (self.clock() + ttl, role, session_id, share_captions)
        return ticket

    def exchange(self, ticket, role):
        record = self.tickets.pop(self.key(ticket), None)
        if not record or record[0] <= self.clock() or record[1] != role:
            raise Fault("TICKET_EXPIRED", "This connection ticket is invalid or already used.", 401)
        return self.issue(role, record[2], record[3])
