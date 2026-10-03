import sys
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest

from backend.jev_client import JevClient


def test_disabled_gates_do_not_spawn_a_warmup_process(monkeypatch):
    from backend import jev_client

    monkeypatch.setattr(jev_client.config, "jev_gates", lambda: False)
    monkeypatch.setattr(jev_client._client, "start", lambda: pytest.fail("Disabled Jev was spawned"))
    assert jev_client.warm() is False


def test_startup_workers_capture_process_before_reader_clears_it(monkeypatch):
    from types import SimpleNamespace

    from backend import jev_client

    process = SimpleNamespace(stdout=StringIO(""), stderr=StringIO(""), poll=lambda: 1, pid=9999)
    monkeypatch.setattr(jev_client.subprocess, "Popen", lambda *args, **kwargs: process)

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(jev_client.threading, "Thread", ImmediateThread)
    with pytest.raises(RuntimeError, match="did not become ready"):
        JevClient().start()


@pytest.fixture
def client():
    rpc = JevClient([sys.executable, "-u", str(Path(__file__).parent / "fixtures/sidecar.py")])
    yield rpc
    rpc.stop()


def test_out_of_order_concurrent_replies_are_correlated(client):
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(client.ask, {"delay": delay, "value": i}, {})
            for i, delay in enumerate([0.15, 0.01, 0.05])
        ]
        assert [f.result()["state"]["value"] for f in futures] == [0, 1, 2]


def test_timeout_does_not_poison_next_request(client):
    with pytest.raises(RuntimeError, match="timed out"):
        client.ask({"delay": 1.2, "value": "expired"}, {}, timeout=0.01)
    response = client.ask({"delay": 0.3, "value": "current"}, {}, timeout=2)
    assert response["state"]["value"] == "current"


def test_stderr_is_drained_and_process_can_restart(client):
    assert client.ask({"noisy": True}, {})["ok"]
    client.stop()
    assert client.ask({"value": "restarted"}, {})["state"]["value"] == "restarted"


def test_stray_stdout_line_does_not_wedge_later_requests(client):
    assert client.ask({"stray_stdout": True, "value": "first"}, {}, timeout=2)["state"]["value"] == "first"
    assert client.ask({"value": "second"}, {}, timeout=2)["state"]["value"] == "second"
