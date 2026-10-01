"""Deterministic subprocess protocol fixture; no provider calls."""

import json
import sys
import threading
import time

lock = threading.Lock()


def reply(request):
    time.sleep(request["state"].get("delay", 0))
    # Enough stderr to fill an undrained pipe on Windows.
    # A dependency printing to stdout must not break the protocol.
    if request["state"].get("stray_stdout"):
        with lock:
            print("warning: not protocol output", flush=True)
    if request["state"].get("noisy"):
        sys.stderr.write("diagnostic\n" * 10000)
        sys.stderr.flush()
    with lock:
        print(json.dumps({"id": request["id"], "ok": True, "state": request["state"]}), flush=True)


print('{"id":"ready"}', flush=True)
for line in sys.stdin:
    threading.Thread(target=reply, args=(json.loads(line),), daemon=True).start()
