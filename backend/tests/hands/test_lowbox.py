"""The box the native browser runs in (lowbox.py) and PANINI's sandbox account (sandbox_user.py)."""

import subprocess
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("The box is Windows-only.", allow_module_level=True)

from backend.hands import lowbox, native_browser, sandbox_user  # noqa: E402


def test_a_boxed_program_runs_in_its_own_home_and_returns_its_exit_code(tmp_path):
    workspace = tmp_path / "box"
    argv = lowbox.command(["cmd.exe", "/d", "/c", "echo %USERPROFILE%> where.txt & exit 3"], workspace)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert done.returncode == 3
    assert (workspace / "where.txt").read_text().strip() == str(workspace / "home")


def test_medium_boxes_keep_the_integrity_chromium_needs_for_its_own_sandbox(tmp_path):
    argv = lowbox.command(["cmd.exe", "/d", "/c", "exit 0"], tmp_path / "box", integrity="medium")
    assert argv[argv.index("--integrity") + 1] == "medium" and "--as-user" not in argv
    assert subprocess.run(argv, capture_output=True, timeout=60).returncode == 0


class _Store:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value


def test_the_sandbox_account_is_used_only_once_the_owner_has_set_it_up(tmp_path, monkeypatch):
    assert sandbox_user.credentials(_Store("")) is None
    assert sandbox_user.credentials(_Store("pw")) == (sandbox_user.ACCOUNT, "pw")

    browser = native_browser.NativeBrowser(lambda c: tmp_path)
    monkeypatch.setattr(sandbox_user, "credentials", lambda store=None: None)
    assert "--as-user" not in browser.boxed(tmp_path)["args"] and browser.isolation == "job-box"
    monkeypatch.setattr(sandbox_user, "credentials", lambda store=None: ("panini-sandbox", "pw"))
    args = browser.boxed(tmp_path)["args"]
    assert "--as-user" in args and browser.isolation == "sandbox-account"
    assert "pw" not in args  # The password never reaches a command line.
