"""PANINI-owned experimental Windows AppContainer command runner.

No Docker or Codex executable is involved. This uses Windows 11's experimental
CreateProcessInSandbox API, so callers must probe and opt in; it is not yet the
default release policy. Output is relayed through a sandbox-granted scratch file
because this API currently requires inheritHandles=False.
"""

import asyncio
import codecs
import ctypes
import os
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from ctypes import wintypes
from pathlib import Path

from .native_probe import PROCESS_INFORMATION, STARTUPINFOW, _spec, _system_library, verify_boundary
from .shell import CommandResult, _WindowsJob, kill_windows_tree

CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STILL_ACTIVE = 259
OUTPUT_KEEP = 256 * 1024
MAX_OUTPUT_FILE_BYTES = 4 * 1024 * 1024
MAX_CHILD_PROCESSES = 64
MAX_JOB_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


def _reject_unsafe_workspace_links(workspace):
    """Fail closed on aliases that can redirect a granted path outside the workspace."""
    workspace = Path(workspace).resolve(strict=True)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if getattr(info, "st_file_attributes", 0) & reparse:
                # Only filesystem links whose behavior is covered by the
                # AppContainer junction/symlink boundary are accepted.
                if getattr(info, "st_reparse_tag", None) not in (0xA0000003, 0xA000000C):
                    raise OSError(f"Native sandbox rejects unsupported workspace reparse point: {path}")
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    # A broken package-manager link cannot be traversed. If it
                    # later gains a target, AppContainer still enforces scope.
                    continue
                if target != workspace and not target.is_relative_to(workspace):
                    raise OSError(f"Native sandbox rejects workspace reparse point: {path}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise OSError(f"Native sandbox rejects workspace hard link: {path}")


def native_environment(scratch, source=None):
    """Pass only toolchain essentials, never arbitrary host environment values."""
    source = os.environ if source is None else source
    permitted = (
        "SystemRoot",
        "WINDIR",
        "PATH",
        "PATHEXT",
        "ComSpec",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "OS",
    )
    variables = {key: source[key] for key in permitted if key in source}
    variables.update(
        TEMP=str(scratch),
        TMP=str(scratch),
        HOME=str(scratch),
        USERPROFILE=str(scratch),
        APPDATA=str(scratch),
        LOCALAPPDATA=str(scratch),
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PAGER="cat",
        GIT_PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
    )
    return variables


def available():
    if os.name != "nt":
        return False, "Native sandbox requires Windows."
    if sys.getwindowsversion().build < 22000:
        return False, "PANINI's native sandbox requires Windows 11; this host is unsupported."
    try:
        library = _system_library("processmodel.dll")
        library.Experimental_CreateProcessInSandbox
    except (OSError, AttributeError):
        return False, "Windows CreateProcessInSandbox API is unavailable."
    return True, "Windows AppContainer API is present; experimental mode."


def _libraries():
    processmodel = _system_library("processmodel.dll", use_last_error=True)
    create = processmodel.Experimental_CreateProcessInSandbox
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    create.restype = wintypes.BOOL
    kernel = _system_library("kernel32.dll", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    userenv = _system_library("userenv.dll")
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    return create, kernel, userenv


def _write_scripts(scratch, command):
    source = scratch / "command.ps1"
    wrapper = scratch / "wrapper.ps1"
    output = scratch / "output.txt"
    source.write_text(command, encoding="utf-8")
    quoted_source = str(source).replace("'", "''")
    quoted_output = str(output).replace("'", "''")
    wrapper.write_text(
        "$ProgressPreference='SilentlyContinue'\n"
        "$global:LASTEXITCODE=$null\n"
        f"& '{quoted_source}' *>&1 | Out-File -LiteralPath '{quoted_output}' -Encoding utf8\n"
        "$paniniOk=$?\n"
        "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "if (-not $paniniOk) { exit 1 }\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return wrapper, output


async def run_command(command, workspace, timeout, on_output=None):
    ready, reason = available()
    if not ready:
        raise OSError(reason)
    ready, reason = await asyncio.to_thread(verify_boundary)
    if not ready:
        raise OSError(f"PANINI native sandbox boundary check failed: {reason}")
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise OSError("Native sandbox workspace must be an existing directory")
    create, kernel, userenv = _libraries()
    started = time.monotonic()
    identity = "PaniniNative_" + uuid.uuid4().hex
    info = PROCESS_INFORMATION()
    job = None
    created = False
    timed_out = False
    output_text = ""
    last_size = 0
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    with tempfile.TemporaryDirectory(prefix="panini-native-run-") as directory:
        scratch = Path(directory).resolve()
        wrapper, output = _write_scripts(scratch, command)
        executable = str(
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        )
        command_line = ctypes.create_unicode_buffer(
            subprocess.list2cmdline(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(wrapper),
                ]
            )
        )
        variables = native_environment(scratch)
        environment = ctypes.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in sorted(variables.items())) + "\0\0"
        )
        # Grant PANINI's own interpreter/toolchain read-only, without granting
        # its entire user profile or writable host environment.
        runtime = Path(sys.prefix).resolve()
        base_runtime = Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent
        spec_bytes = _spec(workspace, scratch, read_only_roots=(runtime, base_runtime))
        spec = ctypes.create_string_buffer(spec_bytes)
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)

        async def collect():
            nonlocal last_size, output_text
            if not output.is_file():
                return
            size = output.stat().st_size
            if size > MAX_OUTPUT_FILE_BYTES:
                raise OSError("Native sandbox output exceeded the 4 MiB limit")
            with output.open("rb") as stream:
                stream.seek(last_size)
                while last_size < size:
                    data = stream.read(min(65536, size - last_size))
                    if not data:
                        break
                    last_size += len(data)
                    text = decoder.decode(data)
                    if text:
                        output_text = (output_text + text)[-OUTPUT_KEEP:]
                        if on_output:
                            await on_output(text)

        try:
            _reject_unsafe_workspace_links(workspace)
            created = bool(
                create(
                    executable,
                    command_line,
                    None,
                    None,
                    False,
                    CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                    environment,
                    str(workspace),
                    ctypes.byref(startup),
                    identity,
                    spec,
                    len(spec_bytes),
                    ctypes.byref(info),
                )
            )
            if not created:
                raise OSError(f"Windows sandbox launch failed: {ctypes.get_last_error()}")
            job = _WindowsJob(
                info.dwProcessId,
                max_processes=MAX_CHILD_PROCESSES,
                max_job_memory=MAX_JOB_MEMORY_BYTES,
            )  # Attach before any user code runs; resumes the process.
            while kernel.WaitForSingleObject(info.hProcess, 0) == 0x102:
                if time.monotonic() - started >= timeout:
                    timed_out = True
                    await asyncio.to_thread(kill_windows_tree, info.dwProcessId, job)
                    break
                await collect()
                await asyncio.sleep(0.1)
            await collect()
            code = wintypes.DWORD(STILL_ACTIVE)
            if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
                raise OSError(f"Windows sandbox exit-code read failed: {ctypes.get_last_error()}")
            if code.value == STILL_ACTIVE:
                await asyncio.to_thread(kill_windows_tree, info.dwProcessId, job)
                stopped = await asyncio.to_thread(kernel.WaitForSingleObject, info.hProcess, 5000)
                if stopped != 0:
                    raise OSError("Native sandbox process did not stop after termination")
            return CommandResult(
                None if timed_out else code.value,
                output_text,
                time.monotonic() - started,
                timed_out,
                max(0, last_size - len(output_text)),
            )
        except BaseException:
            if created:
                await asyncio.to_thread(kill_windows_tree, info.dwProcessId, job)
                await asyncio.to_thread(kernel.WaitForSingleObject, info.hProcess, 5000)
            raise
        finally:
            if job:
                job.close()
            if info.hThread:
                kernel.CloseHandle(info.hThread)
            if info.hProcess:
                kernel.CloseHandle(info.hProcess)
            cleanup = userenv.DeleteAppContainerProfile(identity)
            if cleanup not in (0, -2147024894):  # S_OK or profile not created.
                issue = f"Windows AppContainer profile cleanup failed: 0x{cleanup & 0xFFFFFFFF:08x}"
                if sys.exc_info()[0] is None:
                    raise OSError(issue)
                warnings.warn(issue, ResourceWarning, stacklevel=2)
