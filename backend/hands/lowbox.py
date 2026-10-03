"""Low box: runs a long-lived program (the sandbox browser and its driver) under a restricted Windows token.

Used as a launcher between PANINI and the program, keeping stdio so MCP still works:

    python -m backend.hands.lowbox --workspace W [--memory-mb N] [--allow-read DIR ...] -- program args...

The child runs with:
- **Low integrity**: it cannot write to anything the person owns (Medium) outside the workspace;
- **the person's account and Administrators as deny-only**: ACLs that grant the person or admins (their profile,
  documents, desktop, other drives' private folders) no longer apply, so it cannot read them either. It can still
  read what every user can (Windows, Program Files), plus folders granted with --allow-read;
- **a job object**: kill on close (if PANINI or this launcher ends, everything in the box ends), a per-process
  memory cap and a process cap;
- **a private environment**: TEMP, profile and app-data folders inside the workspace.

The workspace and the --allow-read folders are granted to the session's logon SID, the one group left that the
box can use. This is the Codex Windows pattern (restricted token + ACLs) with Low integrity and a job on top.
"""

import argparse
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .native_sandbox import (
    JOB_LIMITS,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    PROCESS_INFORMATION,
    SID_AND_ATTRIBUTES,
    STARTUPINFOW,
    _check,
    advapi32,
    kernel32,
)

TOKEN_ALL_ACCESS = 0xF01FF
DISABLE_MAX_PRIVILEGE = 0x1
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_DEFAULT_DACL = 6
TOKEN_USER, TOKEN_GROUPS = 1, 2
SE_GROUP_INTEGRITY = 0x20
SE_GROUP_LOGON_ID = 0xC0000000
LOW_INTEGRITY = "S-1-16-4096"
ADMINISTRATORS = "S-1-5-32-544"
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_SUSPENDED = 0x4
STARTF_USESTDHANDLES = 0x100
INFINITE = 0xFFFFFFFF


class TOKEN_MANDATORY_LABEL(ctypes.Structure):  # noqa: N801
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.CreateRestrictedToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SID_AND_ATTRIBUTES),
    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE),
]  # fmt: skip
advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]  # fmt: skip
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
]  # fmt: skip
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
]  # fmt: skip
advapi32.GetSecurityDescriptorDacl.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL),
]  # fmt: skip
advapi32.CreateProcessWithLogonW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
]  # fmt: skip
LOGON_WITH_PROFILE = 0x1
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetStdHandle.restype = wintypes.HANDLE


def _token_info(token, cls) -> ctypes.Array:
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, cls, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    _check(advapi32.GetTokenInformation(token, cls, buffer, size, ctypes.byref(size)), "GetTokenInformation")
    return buffer


def _sid_string(sid) -> str:
    text = wintypes.LPWSTR()
    _check(advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSid")
    return text.value


def identities(token) -> tuple[str, str]:
    """The person's SID and the session's logon SID."""
    user = SID_AND_ATTRIBUTES.from_buffer(_token_info(token, TOKEN_USER))
    groups = _token_info(token, TOKEN_GROUPS)
    count = wintypes.DWORD.from_buffer(groups).value
    entries = (SID_AND_ATTRIBUTES * count).from_buffer(groups, ctypes.sizeof(ctypes.c_void_p))
    logon = next(e for e in entries if e.Attributes & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID)
    return _sid_string(user.Sid), _sid_string(logon.Sid)


def restricted_token(integrity: str = "low"):
    own = wintypes.HANDLE()
    _check(
        advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(own)),
        "OpenToken",
    )
    admins = ctypes.c_void_p()
    _check(advapi32.ConvertStringSidToSidW(ADMINISTRATORS, ctypes.byref(admins)), "ConvertStringSidToSid")
    # Only Administrators is deny-only: denying the person too stops Windows reading their registry hive, and no
    # program can start. Reads of the person's private folders are stopped by No-Read-Up labels instead (guard()).
    deny = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(admins, 0))
    token = wintypes.HANDLE()
    _check(
        advapi32.CreateRestrictedToken(
            own, DISABLE_MAX_PRIVILEGE, 1, deny, 0, None, 0, None, ctypes.byref(token)
        ),
        "CreateRestrictedToken",
    )
    if integrity != "low":
        return own, token
    low = ctypes.c_void_p()
    _check(advapi32.ConvertStringSidToSidW(LOW_INTEGRITY, ctypes.byref(low)), "ConvertStringSidToSid")
    label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(low, SE_GROUP_INTEGRITY))
    size = ctypes.sizeof(label) + 12
    _check(
        advapi32.SetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), size), "SetIntegrity"
    )
    return own, token


def grant(folder: Path, sid: str, rights: str) -> None:
    """Grant the box's logon SID access to a folder (and label writable folders Low)."""
    flags = subprocess.CREATE_NO_WINDOW
    subprocess.run(["icacls", str(folder), "/grant", f"*{sid}:(OI)(CI){rights}", "/Q"], capture_output=True,
                   creationflags=flags, check=True)  # fmt: skip
    if rights != "RX":
        subprocess.run(["icacls", str(folder), "/setintegritylevel", "(OI)(CI)low", "/Q"], capture_output=True,
                       creationflags=flags, check=True)  # fmt: skip


def environment(workspace: Path) -> dict:
    home = workspace / "home"
    keep = ("SystemRoot", "SystemDrive", "windir", "PATH", "PATHEXT", "ComSpec", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
            "ProgramData", "PLAYWRIGHT_BROWSERS_PATH")  # fmt: skip
    env = {k: os.environ[k] for k in keep if k in os.environ}
    for name, sub in (("TEMP", "tmp"), ("TMP", "tmp"), ("USERPROFILE", ""), ("HOME", ""),
                      ("LOCALAPPDATA", "AppData/Local"), ("APPDATA", "AppData/Roaming")):  # fmt: skip
        path = home / sub
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env["USERNAME"] = "panini-sandbox"
    return env


def launch(argv: list[str], workspace: Path, *, memory_mb: int, allow_read: list[Path], integrity="low",
           as_user: bool = False) -> int:  # fmt: skip
    workspace.mkdir(parents=True, exist_ok=True)
    account = None
    if as_user:
        from . import sandbox_user

        account = sandbox_user.credentials()
        if account is None:
            raise SystemExit("PANINI's sandbox account is not set up.")
        sandbox_user.grant(workspace)
        # Files the account creates must stay readable by PANINI (the person), e.g. live-view frames.
        own = wintypes.HANDLE()
        _check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x8, ctypes.byref(own)), "OpenToken")
        person, _ = identities(own)
        subprocess.run(["icacls", str(workspace), "/grant", f"*{person}:(OI)(CI)F", "/Q"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)  # fmt: skip
        for folder in allow_read:
            sandbox_user.grant(folder, "RX")
    else:
        own, token = restricted_token(integrity)
        _, logon = identities(own)
        grant(workspace, logon, "F")
        for folder in allow_read:
            grant(folder, logon, "RX")
    env = environment(workspace)

    job = kernel32.CreateJobObjectW(None, None)
    _check(job, "CreateJobObject")
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_LIMITS
    limits.BasicLimitInformation.ActiveProcessLimit = 96
    limits.ProcessMemoryLimit = memory_mb * 1024 * 1024
    _check(
        kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)), "SetJobLimits"
    )

    info = STARTUPINFOW()
    info.cb = ctypes.sizeof(info)
    info.dwFlags = STARTF_USESTDHANDLES
    info.hStdInput, info.hStdOutput, info.hStdError = (kernel32.GetStdHandle(n) for n in (-10, -11, -12))
    block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    process = PROCESS_INFORMATION()
    flags = CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED
    if account is not None:
        # Its own Windows account: the person's files are closed to it; the password never leaves this process.
        ok = advapi32.CreateProcessWithLogonW(
            account[0], ".", account[1], LOGON_WITH_PROFILE, None, line, flags,
            ctypes.addressof(block), str(workspace), ctypes.byref(info), ctypes.byref(process),
        )  # fmt: skip
        _check(ok, "CreateProcessWithLogon")
    else:
        ok = advapi32.CreateProcessAsUserW(
            token, None, line, None, None, True, flags,
            ctypes.addressof(block), str(workspace), ctypes.byref(info), ctypes.byref(process),
        )  # fmt: skip
        _check(ok, "CreateProcessAsUser")
    if not kernel32.AssignProcessToJobObject(job, process.hProcess):
        kernel32.TerminateProcess(process.hProcess, 1)
        _check(False, "AssignProcessToJobObject")
    kernel32.ResumeThread(process.hThread)
    kernel32.WaitForSingleObject(process.hProcess, INFINITE)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
    return int(code.value)  # Closing the job on exit ends anything the program left behind.


def command(argv: list[str], workspace: Path, *, memory_mb: int = 2048, allow_read=(), integrity="low",
            as_user=False) -> list[str]:  # fmt: skip
    """The launcher command line that runs argv inside a box."""
    extra = [a for folder in allow_read for a in ("--allow-read", str(folder))] + ["--integrity", integrity]
    extra += ["--as-user"] if as_user else []
    return [sys.executable, "-m", "backend.hands.lowbox", "--workspace", str(workspace),
            "--memory-mb", str(memory_mb), *extra, "--", *argv]  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--allow-read", action="append", type=Path, default=[])
    parser.add_argument("--integrity", choices=("low", "medium"), default="low")
    parser.add_argument("--as-user", action="store_true", help="Run as PANINI's sandbox account.")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    return launch(argv, args.workspace, memory_mb=args.memory_mb, allow_read=args.allow_read,
                  integrity=args.integrity, as_user=args.as_user)  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
