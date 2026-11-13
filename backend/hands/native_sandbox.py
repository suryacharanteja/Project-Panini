"""The native Windows sandbox (ADR-0001, Codex-style): sandbox commands with no container engine at all.

Each sandbox computer is a Windows AppContainer (the isolation behind Edge's renderer and UWP apps), launched
through a job object:

- **Files:** an AppContainer opens only objects whose ACL grants its own SID (or ALL APPLICATION PACKAGES, which
  covers Windows' system programs). The computer's workspace is granted to that SID and nothing else of the
  person's is: their profile, documents and other computers' workspaces stay out of reach.
- **Network:** none unless the `internetClient` capability is granted; loopback is blocked for AppContainers even
  then, so sandboxed code can never reach PANINI's own local ports.
- **Integrity:** AppContainers run at low integrity, so they cannot write to the person's medium-integrity objects
  or send window messages to their apps.
- **Limits:** a job object kills the whole process tree when the command ends or overruns, caps memory and the
  number of processes, and blocks clipboard, desktop and system-setting access.

No administrator rights are needed; the only change outside PANINI's data folder is the per-user AppContainer
profile Windows keeps for any packaged app.
"""

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

MAX_OUTPUT = 200_000
INTERNET_CLIENT = "S-1-15-3-1"  # The internetClient capability SID.


class NativeSandboxError(RuntimeError):
    pass


if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class SECURITY_CAPABILITIES(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class STARTUPINFOW(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):  # noqa: N801
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]  # fmt: skip

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):  # noqa: N801
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION),
    ]  # fmt: skip
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x1
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
SE_GROUP_ENABLED = 0x4
JOB_LIMITS = 0x2000 | 0x100 | 0x8 | 0x400  # kill on close, process memory, active processes, die on exception
UI_LIMITS = (
    0xFF  # handles, read/write clipboard, system parameters, display settings, global atoms, desktop, exit
)
WAIT_TIMEOUT = 0x102
ERROR_ALREADY_EXISTS_HR = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)


def available() -> bool:
    return os.name == "nt"


def _check(ok, what):
    if not ok:
        raise NativeSandboxError(f"{what} failed (Windows error {ctypes.get_last_error()}).")


class NativeSandbox:
    """One AppContainer per sandbox computer, each with its own workspace."""

    def __init__(self, root: Path | None = None):
        if root is None:
            from ..extension_pairing import data_directory

            root = data_directory() / "sandbox-native"
        self.root = Path(root)
        self._sids: dict[str, tuple[ctypes.c_void_p, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def profile_name(computer: str) -> str:
        return f"PANINI.Sandbox.{computer}"[:64]

    def sid(self, computer: str) -> tuple[ctypes.c_void_p, str]:
        """The computer's AppContainer SID (created on first use) and its string form."""
        with self._lock:
            if computer in self._sids:
                return self._sids[computer]
            if not available():
                raise NativeSandboxError("The native sandbox needs Windows.")
            sid = ctypes.c_void_p()
            name = self.profile_name(computer)
            hr = userenv.CreateAppContainerProfile(
                name, f"PANINI sandbox {computer}", "PANINI sandbox computer", None, 0, ctypes.byref(sid)
            )
            if hr == ERROR_ALREADY_EXISTS_HR:
                hr = userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid))
            if hr != 0:
                raise NativeSandboxError(
                    f"Windows could not create the sandbox container (0x{hr & 0xFFFFFFFF:08X})."
                )
            text = wintypes.LPWSTR()
            _check(advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSid")
            value = text.value
            kernel32.LocalFree(text)
            self._sids[computer] = (sid, value)
            return self._sids[computer]

    def workspace(self, computer: str) -> Path:
        """The computer's only writable folder, granted to its AppContainer SID alone."""
        folder = self.root / computer / "workspace"
        marker = folder.parent / ".granted"
        _, sid = self.sid(computer)
        if not marker.exists() or marker.read_text() != sid:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "tmp").mkdir(exist_ok=True)
            # icacls changes only PANINI's own folder: full control for this computer's container, inherited.
            done = subprocess.run(
                ["icacls", str(folder), "/grant", f"*{sid}:(OI)(CI)F", "/T", "/Q"],
                capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            )  # fmt: skip
            if done.returncode != 0:
                raise NativeSandboxError(
                    f"Could not prepare the sandbox workspace: {done.stdout or done.stderr}"
                )
            marker.write_text(sid)
        return folder

    # Running commands ------------------------------------------------------------------------------
    def run(self, computer: str, command: str, *, timeout_s: float = 30, memory_mb: int = 1024,
            network: bool = False, max_processes: int = 32, cancel=None) -> dict:  # fmt: skip
        """Run `command` with cmd.exe inside the computer's AppContainer. Returns exitCode, stdout, stderr."""
        if not available():
            raise NativeSandboxError("The native sandbox needs Windows.")
        if cancel is not None and cancel.is_set():
            raise NativeSandboxError("The sandbox command was cancelled before launch.")
        workspace = self.workspace(computer)
        sid, _ = self.sid(computer)
        caps, cap_sids = (SID_AND_ATTRIBUTES * 1)(), []
        count = 0
        if network:
            cap = ctypes.c_void_p()
            _check(
                advapi32.ConvertStringSidToSidW(INTERNET_CLIENT, ctypes.byref(cap)), "ConvertStringSidToSid"
            )
            cap_sids.append(cap)
            caps[0] = SID_AND_ATTRIBUTES(cap, SE_GROUP_ENABLED)
            count = 1
        security = SECURITY_CAPABILITIES(sid, caps if count else None, count, 0)

        owned: list[int] = []  # Handles this call must close.

        def close(handle):
            if handle in owned:
                owned.remove(handle)
                kernel32.CloseHandle(handle)

        try:
            attrs = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
            ends = {}
            for name in ("stdin", "stdout", "stderr"):
                read, write = wintypes.HANDLE(), wintypes.HANDLE()
                _check(
                    kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(attrs), 0),
                    "CreatePipe",
                )
                owned += [read.value, write.value]
                ends[name] = (read.value, write.value)
            child = (ends["stdin"][0], ends["stdout"][1], ends["stderr"][1])
            parent_stdin, parent_stdout, parent_stderr = (
                ends["stdin"][1],
                ends["stdout"][0],
                ends["stderr"][0],
            )
            for handle in (parent_stdin, parent_stdout, parent_stderr):
                kernel32.SetHandleInformation(
                    handle, HANDLE_FLAG_INHERIT, 0
                )  # Only the child's ends are inherited.
            inherit = (wintypes.HANDLE * 3)(*child)

            size = ctypes.c_size_t()
            kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            attribute_list = ctypes.create_string_buffer(size.value)
            _check(
                kernel32.InitializeProcThreadAttributeList(attribute_list, 2, 0, ctypes.byref(size)),
                "InitializeProcThreadAttributeList",
            )
            _check(kernel32.UpdateProcThreadAttribute(attribute_list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                                      ctypes.byref(security), ctypes.sizeof(security), None, None), "UpdateProcThreadAttribute")  # fmt: skip
            _check(kernel32.UpdateProcThreadAttribute(attribute_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                                      inherit, ctypes.sizeof(inherit), None, None), "UpdateProcThreadAttribute")  # fmt: skip

            info = STARTUPINFOEXW()
            info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            info.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            info.StartupInfo.hStdInput, info.StartupInfo.hStdOutput, info.StartupInfo.hStdError = child
            info.lpAttributeList = ctypes.cast(attribute_list, ctypes.c_void_p)

            system = os.environ.get("SystemRoot", r"C:\Windows")
            env = {
                "SystemRoot": system,
                "windir": system,
                "ComSpec": rf"{system}\System32\cmd.exe",
                "PATH": rf"{system}\System32;{system};{system}\System32\WindowsPowerShell\v1.0",
                "PATHEXT": ".COM;.EXE;.BAT;.CMD;.PS1",
                "TEMP": str(workspace / "tmp"),
                "TMP": str(workspace / "tmp"),
                "USERPROFILE": str(workspace),
                "HOMEDRIVE": str(workspace)[:2],
                "HOMEPATH": str(workspace)[2:],
                "PSModulePath": rf"{system}\System32\WindowsPowerShell\v1.0\Modules",
                # Windows' AppContainer launch looks these up (error 203 without them); all point into the workspace.
                "SystemDrive": system[:2],
                "LOCALAPPDATA": str(workspace / "AppData" / "Local"),
                "APPDATA": str(workspace / "AppData" / "Roaming"),
                "ProgramData": str(workspace / "ProgramData"),
                "USERNAME": "panini-sandbox",
                "COMPUTERNAME": "PANINI-SANDBOX",
            }
            block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")
            line = ctypes.create_unicode_buffer(rf'"{system}\System32\cmd.exe" /d /s /c "{command}"')

            job = kernel32.CreateJobObjectW(None, None)
            _check(job, "CreateJobObject")
            owned.append(job)
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_LIMITS
            limits.BasicLimitInformation.ActiveProcessLimit = max_processes
            limits.ProcessMemoryLimit = memory_mb * 1024 * 1024
            _check(
                kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                "SetInformationJobObject",
            )
            ui = JOBOBJECT_BASIC_UI_RESTRICTIONS(UI_LIMITS)
            _check(
                kernel32.SetInformationJobObject(job, 4, ctypes.byref(ui), ctypes.sizeof(ui)),
                "SetInformationJobObject",
            )

            process = PROCESS_INFORMATION()
            flags = (
                EXTENDED_STARTUPINFO_PRESENT
                | CREATE_SUSPENDED
                | CREATE_NO_WINDOW
                | CREATE_UNICODE_ENVIRONMENT
            )
            ok = kernel32.CreateProcessW(
                None,
                line,
                None,
                None,
                True,
                flags,
                ctypes.addressof(block),
                str(workspace),
                ctypes.byref(info),
                ctypes.byref(process),
            )
            kernel32.DeleteProcThreadAttributeList(attribute_list)
            _check(ok, "CreateProcess")
            owned += [process.hProcess, process.hThread]
            if not kernel32.AssignProcessToJobObject(job, process.hProcess):
                kernel32.TerminateProcess(process.hProcess, 1)
                _check(False, "AssignProcessToJobObject")
            for handle in (*child, parent_stdin):
                close(handle)  # The child owns its ends now; closing ours lets reads end when it exits.

            output = {"stdout": bytearray(), "stderr": bytearray()}

            def drain(handle, sink):
                buffer = ctypes.create_string_buffer(65536)
                read = wintypes.DWORD()
                while kernel32.ReadFile(handle, buffer, 65536, ctypes.byref(read), None) and read.value:
                    if len(sink) < MAX_OUTPUT:
                        sink.extend(buffer.raw[: read.value])

            readers = [
                threading.Thread(target=drain, args=(parent_stdout, output["stdout"]), daemon=True),
                threading.Thread(target=drain, args=(parent_stderr, output["stderr"]), daemon=True),
            ]
            for reader in readers:
                reader.start()
            started = time.monotonic()
            if cancel is None or not cancel.is_set():
                kernel32.ResumeThread(process.hThread)
            deadline = started + timeout_s
            cancelled = False
            waited = WAIT_TIMEOUT
            while True:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                waited = kernel32.WaitForSingleObject(process.hProcess, 50)
                if waited != WAIT_TIMEOUT or time.monotonic() >= deadline:
                    break
            timed_out = not cancelled and waited == WAIT_TIMEOUT
            kernel32.TerminateJobObject(job, 1)  # Ends every process the command started, so the pipes close.
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
            for reader in readers:
                reader.join(5)
            return {
                "exitCode": None if timed_out or cancelled else int(code.value),
                "timedOut": timed_out,
                "cancelled": cancelled,
                "stdout": output["stdout"].decode("utf-8", errors="replace")[:MAX_OUTPUT],
                "stderr": output["stderr"].decode("utf-8", errors="replace")[:MAX_OUTPUT],
                "seconds": round(time.monotonic() - started, 3),
            }
        finally:
            for handle in list(owned):
                close(handle)
            for cap in cap_sids:
                advapi32.FreeSid(cap)

    # Files ----------------------------------------------------------------------------------------------
    def _resolve(self, computer: str, relative: str) -> Path:
        workspace = self.workspace(computer)
        target = (workspace / relative).resolve()
        if not target.is_relative_to(workspace.resolve()):
            raise NativeSandboxError("Use a relative workspace path without traversal.")
        return target

    def files_list(self, computer: str, relative: str = "") -> dict:
        folder = self._resolve(computer, relative)
        if not folder.is_dir():
            raise NativeSandboxError("No such folder in the sandbox workspace.")
        return {"entries": [{"name": p.name, "dir": p.is_dir(), "size": p.stat().st_size if p.is_file() else None}
                            for p in sorted(folder.iterdir()) if p.name != "tmp" or relative]}  # fmt: skip

    def files_read(self, computer: str, relative: str) -> dict:
        path = self._resolve(computer, relative)
        if not path.is_file():
            raise NativeSandboxError("No such file in the sandbox workspace.")
        return {"contents": path.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT]}

    def files_write(self, computer: str, relative: str, contents: str, append: bool = False) -> dict:
        path = self._resolve(computer, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(contents)
        return {"written": len(contents)}
