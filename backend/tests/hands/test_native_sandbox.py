"""The native Windows sandbox (ADR-0001): commands run in a per-computer AppContainer under a job object."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("The native sandbox is Windows-only.", allow_module_level=True)

from backend.hands.native_sandbox import NativeSandbox, NativeSandboxError  # noqa: E402


@pytest.fixture
def box(tmp_path):
    return NativeSandbox(tmp_path / "native")


def test_commands_run_at_low_integrity_in_the_workspace(box):
    result = box.run("t1", "echo hello & cd", timeout_s=20)
    assert result["exitCode"] == 0 and "hello" in result["stdout"]
    assert str(box.workspace("t1")).lower() in result["stdout"].lower()
    assert "Low Mandatory Level" in box.run("t1", "whoami /groups", timeout_s=20)["stdout"]
    assert box.run("t1", "exit 7", timeout_s=10)["exitCode"] == 7


def test_files_round_trip_between_commands_and_the_files_api(box):
    assert box.run("t1", "echo from inside> note.txt", timeout_s=10)["exitCode"] == 0
    assert box.files_read("t1", "note.txt")["contents"].strip() == "from inside"
    box.files_write("t1", "dir/plan.txt", "hello")
    assert box.run("t1", "type dir\\plan.txt", timeout_s=10)["stdout"].strip() == "hello"
    assert any(e["name"] == "note.txt" for e in box.files_list("t1")["entries"])
    with pytest.raises(NativeSandboxError):
        box.files_read("t1", "../outside.txt")


def test_each_computer_has_its_own_container_and_workspace(box):
    assert box.sid("a")[1] != box.sid("b")[1]
    assert box.workspace("a") != box.workspace("b")


def test_a_command_that_overruns_is_ended(box):
    result = box.run("t1", "for /L %i in (1,0,2) do @rem", timeout_s=1)
    assert result["timedOut"] and result["exitCode"] is None and result["seconds"] < 5


def test_memory_is_capped(box):
    grab = "powershell -NoProfile -Command \"$ErrorActionPreference = 'Stop'; $a = New-Object byte[] 800MB; $a[0] = 1; echo done\""
    result = box.run("t1", grab, timeout_s=60, memory_mb=256)
    assert "done" not in result["stdout"] and "OutOfMemory" in result["stderr"]
    assert result["exitCode"] != 0
