"""P-256 primitives in the formats browsers use (V2-14 §5.2).

WebCrypto exports public keys as raw uncompressed points (65 bytes) and ECDSA signatures as r||s (64 bytes);
`cryptography` uses DER for signatures, so this module converts. Everything on the wire is base64url without
padding.
"""

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CURVE = ec.SECP256R1()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(CURVE)


def private_bytes(key) -> str:
    return b64(key.private_numbers().private_value.to_bytes(32, "big"))


def load_private(text: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64(text), "big"), CURVE)


def public_raw(key) -> str:
    public = key.public_key() if hasattr(key, "public_key") else key
    return b64(public.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def load_public(text: str) -> ec.EllipticCurvePublicKey:
    raw = unb64(text)
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("A P-256 public key is 65 bytes, uncompressed.")
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)


def sign(key, message: bytes) -> str:
    r, s = decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
    return b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify(public_text: str, message: bytes, signature: str) -> bool:
    try:
        raw = unb64(signature)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        load_public(public_text).verify(der, message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def ecdh(private, public_text: str) -> bytes:
    return private.exchange(ec.ECDH(), load_public(public_text))


def hkdf(material: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(hashes.SHA256(), length, salt, info).derive(material)


def seal(key: bytes, iv: bytes, plaintext: bytes, aad: bytes) -> bytes:
    return AESGCM(key).encrypt(iv, plaintext, aad)


def open_(key: bytes, iv: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(iv, ciphertext, aad)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()
