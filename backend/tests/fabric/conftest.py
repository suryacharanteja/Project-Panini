import pytest

from backend.fabric import crypto
from backend.fabric.devices import Devices, PcIdentity
from backend.fabric.envelope import DeviceKeys
from backend.harness.store import Store


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def fabric(tmp_path):
    store = Store(tmp_path / "f.sqlite3")
    secrets = MemorySecrets()
    identity = PcIdentity.load(secrets)
    devices = Devices(store, identity)
    yield devices, identity, secrets
    store.close()


def paired(devices, name="Phone"):
    """A device that paired and was confirmed; returns its keys."""
    sign, agree = crypto.new_key(), crypto.new_key()
    code = devices.new_code()["code"]
    answer = devices.pair(code, name, crypto.public_raw(sign), crypto.public_raw(agree))
    devices.confirm(answer["device_id"])
    return DeviceKeys(answer["device_id"], sign, agree)
