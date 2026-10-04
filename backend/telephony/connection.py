"""Accepted SIP audio reuses the Live sideband, with DTMF kept out of transcripts."""

from panini.live import LiveConnection


class PhoneConnection(LiveConnection):
    def __init__(self, *args, on_digits, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_digits = on_digits

    async def handle_event(self, event):
        if event.get("type") == "transport.dtmf.received":
            await self.on_digits(str(event.get("event", "")))
            return
        if event.get("type") == "session.closed":
            await super().handle_event(event)
            await self.on_event({"type": "call.closed", "usage": self.usage})
            return
        await super().handle_event(event)
