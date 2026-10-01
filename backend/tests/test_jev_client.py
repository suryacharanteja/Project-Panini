import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.jev_client import JevClient


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
