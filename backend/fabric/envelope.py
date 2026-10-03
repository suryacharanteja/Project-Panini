"""Sessions and envelopes (V2-14 §5.3): the same on every transport.

Handshake (mutual):
    device -> PC : hello   {device_id, eph, ts, sig_d(eph | ts | pc_id)}
    PC -> device : welcome {eph, ts, sig_p(eph_p | eph_d | device_id)}
    key = HKDF-SHA256(ECDH(eph_d, eph_p) | ECDH(agree_d, agree_p), salt = pc_id | device_id, info = "panini-fabric-v1")

The ephemeral pair gives forward secrecy; the static agreement keys bind the session to the paired device.
Messages: {v: 1, seq, kind: req|res|evt, id, iv, ct}, AES-256-GCM with AAD "v|seq|kind|id". Each direction has a
monotonic seq (gaps allowed, repeats rejected). Sessions expire after 12 hours or when the device is revoked.
"""

import json
import secrets
import time
from dataclasses import dataclass, field

from . import crypto

VERSION = 1
INFO = b"panini-fabric-v1"
SESSION_TTL_S = 12 * 3600
CLOCK_SKEW_S = 120
KINDS = {"req", "res", "evt"}


class EnvelopeError(ValueError):
    pass


def derive_key(
    eph_private, eph_peer: str, agree_private, agree_peer: str, pc_id: str, device_id: str
) -> bytes:
    material = crypto.ecdh(eph_private, eph_peer) + crypto.ecdh(agree_private, agree_peer)
    return crypto.hkdf(material, (pc_id + device_id).encode(), INFO)


def aad(v: int, seq: int, kind: str, msg_id: str) -> bytes:
    return f"{v}|{seq}|{kind}|{msg_id}".encode()


@dataclass
class Session:
    key: bytes
    device_id: str
    started: float
    send_prefix: bytes  # 4 bytes; with seq makes a unique IV per direction
    recv_prefix: bytes
    send_seq: int = 0
    recv_seq: int = -1
    revoked: bool = False
    clock: object = field(default=time.time, repr=False)

    def expired(self) -> bool:
        return self.revoked or self.clock() - self.started > SESSION_TTL_S

    def seal(self, kind: str, body: dict, msg_id: str | None = None) -> dict:
        if self.expired():
            raise EnvelopeError("The session expired.")
        if kind not in KINDS:
            raise EnvelopeError("Unknown message kind.")
        self.send_seq += 1
        msg_id = msg_id or secrets.token_hex(8)
        iv = self.send_prefix + self.send_seq.to_bytes(8, "big")
        plaintext = json.dumps(body, separators=(",", ":")).encode()
        ct = crypto.seal(self.key, iv, plaintext, aad(VERSION, self.send_seq, kind, msg_id))
        return {
            "v": VERSION,
            "seq": self.send_seq,
            "kind": kind,
            "id": msg_id,
            "iv": crypto.b64(iv),
            "ct": crypto.b64(ct),
        }

    def open(self, message: dict) -> tuple[str, str, dict]:
        if self.expired():
            raise EnvelopeError("The session expired.")
        try:
            v, seq, kind, msg_id = (
                int(message["v"]),
                int(message["seq"]),
                str(message["kind"]),
                str(message["id"]),
            )
            iv, ct = crypto.unb64(message["iv"]), crypto.unb64(message["ct"])
        except (KeyError, TypeError, ValueError):
            raise EnvelopeError("Malformed envelope.") from None
        if v != VERSION or kind not in KINDS:
            raise EnvelopeError("Unsupported envelope.")
        if seq <= self.recv_seq:
            raise EnvelopeError("Replayed or reordered message.")
        if iv != self.recv_prefix + seq.to_bytes(8, "big"):
            raise EnvelopeError("Unexpected IV.")
        try:
            plaintext = crypto.open_(self.key, iv, ct, aad(v, seq, kind, msg_id))
        except Exception:  # noqa: BLE001 -- Any decryption failure is the same refusal.
            raise EnvelopeError("The message failed authentication.") from None
        self.recv_seq = seq
        return kind, msg_id, json.loads(plaintext)


# Device side (Python reference; the phone app does the same in WebCrypto) -------------------------------
@dataclass
class DeviceKeys:
    device_id: str
    sign_key: object
    agree_key: object


def hello(device: DeviceKeys, pc_id: str, *, clock=time.time) -> tuple[dict, object]:
    eph = crypto.new_key()
    eph_pub, ts = crypto.public_raw(eph), int(clock())
    signature = crypto.sign(device.sign_key, f"{eph_pub}|{ts}|{pc_id}".encode())
    return {"device_id": device.device_id, "eph": eph_pub, "ts": ts, "sig": signature}, eph


def finish(device: DeviceKeys, eph, message: dict, pc_id: str, pc_sign_pub: str, pc_agree_pub: str,
           *, clock=time.time) -> Session:  # fmt: skip
    """The device checks the PC's welcome and derives the session."""
    statement = f"{message['eph']}|{crypto.public_raw(eph)}|{device.device_id}".encode()
    if not crypto.verify(pc_sign_pub, statement, message["sig"]):
        raise EnvelopeError("The PC's welcome is not signed by your PC.")
    key = derive_key(eph, message["eph"], device.agree_key, pc_agree_pub, pc_id, device.device_id)
    return Session(key, device.device_id, clock(), b"dev\x00", b"pc\x00\x00", clock=clock)


# PC side ---------------------------------------------------------------------------------------
def welcome(identity, devices, message: dict, *, clock=time.time) -> tuple[dict, Session]:
    """The PC checks the device's hello against its registered keys and answers."""
    try:
        device_id, eph_d, ts, sig = (
            str(message["device_id"]),
            str(message["eph"]),
            int(message["ts"]),
            str(message["sig"]),
        )
    except (KeyError, TypeError, ValueError):
        raise EnvelopeError("Malformed hello.") from None
    device = devices.active(device_id)
    if device is None:
        raise EnvelopeError("Unknown or revoked device.")
    if abs(clock() - ts) > CLOCK_SKEW_S:
        raise EnvelopeError("The device clock is too far off.")
    if not crypto.verify(device["sign_pub"], f"{eph_d}|{ts}|{identity.pc_id}".encode(), sig):
        raise EnvelopeError("The hello is not signed by this device.")
    eph = crypto.new_key()
    eph_p = crypto.public_raw(eph)
    signature = crypto.sign(identity.sign_key, f"{eph_p}|{eph_d}|{device_id}".encode())
    key = derive_key(eph, eph_d, identity.agree_key, device["agree_pub"], identity.pc_id, device_id)
    devices.seen(device_id)
    session = Session(key, device_id, clock(), b"pc\x00\x00", b"dev\x00", clock=clock)
    return {"eph": eph_p, "ts": int(clock()), "sig": signature}, session
