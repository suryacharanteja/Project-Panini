"""Opt-in live voice probe; synthetic silence, no person's microphone or phone calls."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "panini-core/voice/live-spoken-conversation"))


async def main():
    from backend import config
    from backend.panini.voice import VoiceMedia

    report = {
        "at": datetime.now(UTC).isoformat(),
        "kind": "live synthetic WebRTC startup and output",
        "voices": [],
        "physical_phone": False,
    }
    if not config.configured("OPENAI_API_KEY"):
        report["blocked"] = "OpenAI credential is not configured."
    else:
        os.environ["PANINI_PERSONAS"] = "true"

        class Personas:
            def get(self, persona_id):
                return {
                    "voice": persona_id,
                    "instructions": "You are a test specialist. Say only: PANINI voice test ready.",
                }

            def presentation(self, persona_id):
                return {"id": persona_id, "name": persona_id.title(), "color": "cyan"}

        owner = SimpleNamespace(
            adapters={"agent": SimpleNamespace(harness=SimpleNamespace(personas=Personas()))}
        )
        for voice in ("marin", "cedar"):
            peer = await asyncio.create_subprocess_exec(
                "node",
                "scripts/panini-voice-peer.mjs",
                cwd=ROOT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            media = VoiceMedia(
                owner,
                SimpleNamespace(
                    voice_persona_id=voice,
                    state="ready",
                    voice_history={
                        voice: [
                            {"role": "user", "text": "This is the PANINI voice test."},
                            {"role": "assistant", "text": "Ready to test the voice connection."},
                        ]
                    },
                ),
                None,
                0,
            )
            output = asyncio.Event()

            async def event(e):
                if e.get("type") == "transcript" and e.get("role") == "assistant" and e.get("delta"):
                    output.set()

            media.connection.on_event = event
            entry = {"voice": voice, "passed": False}
            try:
                async with asyncio.timeout(60):
                    entry["stage"] = "synthetic_offer"
                    offer = json.loads(await peer.stdout.readline())
                    entry["stage"] = "provider_start"
                    answer = await media.start(offer["sdp"])
                    peer.stdin.write((json.dumps({"sdp": answer}) + "\n").encode())
                    await peer.stdin.drain()
                    entry["stage"] = "webrtc_connect"
                    connected = json.loads(await peer.stdout.readline())
                    if not connected.get("connected"):
                        raise RuntimeError("Synthetic audio peer did not connect.")
                    await media.connection.speak(
                        "Backend outcome: PANINI voice test connected. Say PANINI voice test ready."
                    )
                    entry["stage"] = "spoken_output"
                    await asyncio.wait_for(output.wait(), 30)
                    entry["passed"] = True
            except Exception as exc:
                entry["error"] = str(exc)[:350] if isinstance(exc, RuntimeError) else type(exc).__name__
            finally:
                await media.close()
                if peer.returncode is None:
                    peer.stdin.write(b'{"end":true}\n')
                    await peer.stdin.drain()
                    try:
                        await asyncio.wait_for(peer.wait(), 8)
                    except TimeoutError:
                        peer.kill()
                        await peer.wait()
            report["voices"].append(entry)
            if not entry["passed"]:
                break
    target = ROOT / "artifacts/personas/voice-live.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        report["previous_attempts"] = (
            previous.get("previous_attempts", [])
            + [{key: previous[key] for key in ("at", "voices", "kind") if key in previous}]
        )[-5:]
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if len(report["voices"]) == 2 and all(row["passed"] for row in report["voices"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
