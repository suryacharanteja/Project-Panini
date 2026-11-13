"""Verified generic fabric ingress into the trigger bus."""

import json

from ..rules import SLUG, TriggerEvent


def handler(bus):
    async def receive(hook_id, headers, body):
        if not bus.enabled:
            return {"status": 202, "body": "disabled"}
        name = hook_id.removeprefix("generic:")
        if not hook_id.startswith("generic:") or not SLUG.fullmatch(name) or len(body) > 65536:
            return {"status": 413, "body": "invalid hook"}
        try:
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"fields"}
                or not isinstance(payload["fields"], dict)
            ):
                raise ValueError("Invalid webhook event.")
            delivery = next(
                (value for key, value in headers.items() if key.lower() == "x-panini-delivery"), None
            )
            fields = {
                k: v
                for k, v in payload["fields"].items()
                if isinstance(k, str)
                and not any(
                    s in k.casefold() for s in ("password", "secret", "token", "authorization", "credential")
                )
            }
            event = TriggerEvent(
                "webhook",
                "received",
                {"hook": name, "fields": fields},
                bus.clock(),
                f"{hook_id}:{delivery}",
                True,
            )
        except (ValueError, TypeError):
            return {"status": 400, "body": "invalid event"}
        await bus.emit(event)
        return {"status": 202, "body": "accepted"}

    return receive
