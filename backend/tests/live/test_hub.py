"""V2-12 live hub: newest frame wins, bounded rate, viewer counts drive whether sources produce frames."""

import asyncio
import contextlib

import pytest

from backend.live.hub import LiveHub


def test_latest_frame_and_ingest_tokens():
    hub = LiveHub()
    assert hub.latest("managed", "default") is None
    token = hub.ingest_token("managed", "default")
    assert hub.resolve(token) == ("managed", "default") and hub.resolve("wrong") is None
    hub.publish("run-1", "managed", "default", b"one", {"url": "https://a.example"})
    hub.publish("run-1", "managed", "default", b"two", {"url": "https://b.example"})
    jpeg, meta = hub.latest("managed", "default")
    assert jpeg == b"two" and meta["seq"] == 2 and meta["url"] == "https://b.example"
    assert [m["seq"] for m in hub.history("managed", "default")] == [1, 2]
    hub.revoke(token)
    assert hub.resolve(token) is None
    with pytest.raises(ValueError):
        hub.publish(None, "toaster", "x", b"")


def test_subscribers_get_the_newest_frame_at_a_bounded_rate_and_are_counted():
    async def scenario():
        hub = LiveHub()
        seen = []

        async def watch():
            async with contextlib.aclosing(hub.subscribe("sandbox", "panini", max_fps=5)) as frames:
                async for frame in frames:
                    seen.append(frame["seq"])
                    if len(seen) == 2:
                        return

        task = asyncio.ensure_future(watch())
        await asyncio.sleep(0.01)
        assert hub.viewers("sandbox", "panini") == 1 and hub.watched("sandbox", "panini")
        for n in range(10):  # A flood: only the newest survives between deliveries.
            hub.publish(None, "sandbox", "panini", f"f{n}".encode())
        await asyncio.sleep(0.05)
        hub.publish(None, "sandbox", "panini", b"last")
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(task, 2)
        elapsed = asyncio.get_running_loop().time() - started
        return seen, elapsed, hub.viewers("sandbox", "panini")

    seen, elapsed, viewers = asyncio.run(scenario())
    assert seen == [10, 11]  # Frames 1-9 were dropped, never queued.
    assert elapsed >= 0.1  # 5 fps: the second frame waited for the interval.
    assert viewers == 0


def test_watching_lingers_for_the_grace_period_then_stops():
    now = [100.0]
    hub = LiveHub(clock=lambda: now[0])
    assert not hub.watched("desktop", "pc")
    entry = hub._target("desktop", "pc")
    entry.last_view = 100.0
    now[0] = 103.0
    assert hub.watched("desktop", "pc")
    now[0] = 106.0
    assert not hub.watched("desktop", "pc")


def test_a_polling_viewer_counts_as_watching_and_starts_pull_sources():
    async def scenario():
        hub = LiveHub()
        polled = []

        async def source(target):
            polled.append(target)
            return b"png", {"url": "https://example.com"}

        hub.register_poller("sandbox", source, 0.05)
        hub.touch("sandbox", "panini")
        assert hub.watched("sandbox", "panini")
        await asyncio.sleep(0.2)
        return polled, hub.latest("sandbox", "panini")

    polled, latest = asyncio.run(scenario())
    assert polled and polled[0] == "panini" and latest[0] == b"png"
