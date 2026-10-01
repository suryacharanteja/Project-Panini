"""The optional Gradium frontend owns audio; this adapter shares conversation state."""


class ConsoleConnection:
    model = "Gradium via LiveKit"
    usage = None

    def __init__(self, *args, **kwargs):
        pass

    async def create(self, sdp):
        return ""

    async def speak(self, text, delegation_id=None):
        # ConsoleBridge receives the same structured message on the event stream.
        pass

    async def close(self):
        pass
