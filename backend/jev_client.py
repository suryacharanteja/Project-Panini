"""Concurrent, correlated JSON-line RPC to one warm Node evaluation sidecar."""

import json
import logging
import os
import subprocess
import threading
import uuid
from concurrent.futures import Future, TimeoutError

from . import config

logger = logging.getLogger(__name__)


class JevClient:
    def __init__(self, command=None):
        self.command = command or [
            os.getenv("PANINI_NODE", "node"),
            os.getenv("PANINI_JEV_SIDECAR", str(config.ROOT / "frontend" / "jev_sidecar.mjs")),
        ]
        self._process = None
        self._lock = threading.RLock()
        self._pending = {}
        self._ready = None

    def _read(self, process, ready):
        try:
            for line in process.stdout:
                try:
                    response = json.loads(line)
                except ValueError:
                    # A dependency writing to stdout must not end the protocol.
                    logger.warning("Ignoring non-protocol sidecar output")
                    continue
                if not isinstance(response, dict):
                    continue
                if response.get("id") == "ready":
                    ready.set_result(True)
                    continue
                with self._lock:
                    future = self._pending.pop((process.pid, response.get("id")), None)
                # Late replies belong to expired requests, never to the next caller.
                if future is not None:
                    future.set_result(response)
        except (OSError, ValueError) as exc:
            logger.warning("Jev sidecar protocol failed: %s", exc)
        finally:
            if not ready.done():
                ready.set_exception(RuntimeError("Jev sidecar failed to start"))
            with self._lock:
                failed = [key for key in self._pending if key[0] == process.pid]
                for key in failed:
                    self._pending.pop(key).set_exception(RuntimeError("Jev sidecar disconnected"))
                # Nobody reads this process any more; never hand it to a new caller.
                if self._process is process:
                    self._process = None
            if process.poll() is None:
                process.kill()

    @staticmethod
    def _drain_stderr(process):
        for line in process.stderr:
            logger.debug("Jev sidecar: %s", line.rstrip()[:500])

    def start(self):
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = subprocess.Popen(
                    self.command,
                    cwd=config.ROOT / "frontend",
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    env=os.environ.copy(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._ready = Future()
                # The reader may clear self._process immediately when startup fails. Both workers
                # must retain the same process, rather than read mutable state between starts.
                process, ready = self._process, self._ready
                threading.Thread(target=self._read, args=(process, ready), daemon=True).start()
                threading.Thread(target=self._drain_stderr, args=(process,), daemon=True).start()
            else:
                process, ready = self._process, self._ready
        try:
            ready.result(timeout=10)
        except Exception:
            self.stop()
            raise RuntimeError("Jev sidecar did not become ready; check Node dependencies") from None
        return process

    def ask(self, state, questions, timeout=config.PROVIDER_TIMEOUT):
        process = self.start()
        request_id = uuid.uuid4().hex
        key = (process.pid, request_id)
        future = Future()
        try:
            with self._lock:
                self._pending[key] = future
                process.stdin.write(
                    json.dumps(
                        {
                            "id": request_id,
                            "state": state,
                            "questions": questions,
                            "timeoutMs": int(timeout * 1000),
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
            response = future.result(timeout=timeout + 1)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Jev evaluation failed"))
            return response
        except TimeoutError:
            raise RuntimeError(f"Jev evaluation timed out after {timeout}s") from None
        except (BrokenPipeError, OSError):
            raise RuntimeError("Jev sidecar disconnected during request") from None
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def stop(self):
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin:
            process.stdin.close()

    def status(self):
        return bool(
            self._process
            and self._process.poll() is None
            and self._ready.done()
            and self._ready.exception() is None
        )


_client = JevClient()


def ask(state, questions, timeout=config.PROVIDER_TIMEOUT):
    if os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "private":
        raise RuntimeError("Jev cloud checks are unavailable in private mode.")
    if not config.configured("AI_GATEWAY_API_KEY"):
        raise RuntimeError("AI_GATEWAY_API_KEY is not configured")
    return _client.ask(state, questions, timeout)


def warm():
    if not config.jev_gates():
        return False
    try:
        _client.start()
        return True
    except (RuntimeError, OSError) as exc:
        logger.warning("Jev warm-up failed: %s", exc)
        return False


stop = _client.stop
status = _client.status
