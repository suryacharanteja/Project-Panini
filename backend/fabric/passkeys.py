"""WebAuthn bound to the paired device and exact signed decision; no biometric data stored."""

import hashlib
import ipaddress
import json
import secrets
import time
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)


class Passkeys:
    def __init__(self, store, devices, origin, *, clock=time.time):
        self.store, self.devices, self.origin, self.clock = store, devices, origin, clock

    def configuration(self, requested):
        configured = str(self.origin() or "").rstrip("/")
        parsed = urlsplit(configured)
        try:
            ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            pass
        else:
            raise ValueError("Passkeys require a DNS host name, not an IP address.")
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "localhost"))
        ):
            raise ValueError("Configure a stable HTTPS passkey origin in Remote settings.")
        if requested != configured:
            raise ValueError("This app origin does not match the configured passkey origin.")
        return configured, parsed.hostname

    def list(self, device_id):
        return self.store.all(
            "SELECT credential_id,created,origin FROM device_passkeys WHERE device_id=?", (device_id,)
        )

    def begin(self, device_id, origin, *, statement=None):
        origin, rp_id = self.configuration(origin)
        device = self.devices.active(device_id)
        if not device:
            raise ValueError("This phone was removed.")
        keys = self.store.all("SELECT * FROM device_passkeys WHERE device_id=?", (device_id,))
        if len(keys) >= 8 and statement is None:
            raise ValueError("Remove an existing passkey before adding another.")
        ceremony_id = secrets.token_urlsafe(24)
        challenge = hashlib.sha256(ceremony_id.encode() + (statement or secrets.token_bytes(32))).digest()
        descriptors = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(k["credential_id"]))
            for k in keys
            if k["origin"] == origin
        ]
        if statement is None:
            options = generate_registration_options(
                rp_id=rp_id,
                rp_name="PANINI",
                user_id=device_id.encode(),
                user_name=device_id,
                user_display_name=device["name"],
                challenge=challenge,
                exclude_credentials=descriptors,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    user_verification=UserVerificationRequirement.REQUIRED
                ),
            )
            kind = "register"
        else:
            if not descriptors:
                raise ValueError("Add a passkey in Settings on this app origin first.")
            options = generate_authentication_options(
                rp_id=rp_id,
                challenge=challenge,
                allow_credentials=descriptors,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
            kind = "decision"
        with self.store.lock:
            self.store.execute("DELETE FROM passkey_ceremonies WHERE expires<=? OR used=1", (self.clock(),))
            self.store.execute(
                "DELETE FROM passkey_ceremonies WHERE device_id=? AND kind=?", (device_id, kind)
            )
            self.store.execute(
                "INSERT INTO passkey_ceremonies(id,device_id,kind,challenge,origin,rp_id,statement_hash,expires) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ceremony_id,
                    device_id,
                    kind,
                    challenge,
                    origin,
                    rp_id,
                    hashlib.sha256(statement).hexdigest() if statement is not None else None,
                    self.clock() + 120,
                ),
            )
        return {"ceremony_id": ceremony_id, "options": json.loads(options_to_json(options))}

    def finish(self, device_id, ceremony_id, credential, *, statement=None):
        # Verification and consumption are serialized; invalid assertions consume the ceremony too.
        with self.store.lock:
            row = self.store.one(
                "SELECT * FROM passkey_ceremonies WHERE id=? AND device_id=?", (ceremony_id, device_id)
            )
            if not row or row["used"] or row["expires"] <= self.clock() or not self.devices.active(device_id):
                raise ValueError("Passkey request expired or was already used.")
            self.store.execute("UPDATE passkey_ceremonies SET used=1 WHERE id=?", (ceremony_id,))
            expected_kind = "register" if statement is None else "decision"
            expected_hash = hashlib.sha256(statement).hexdigest() if statement is not None else None
            if row["kind"] != expected_kind or row["statement_hash"] != expected_hash:
                raise ValueError("The decision changed. Review it again.")
            if row["origin"] != str(self.origin() or "").rstrip("/"):
                raise ValueError("The passkey origin changed. Start again.")
            if len(json.dumps(credential)) > 50000:
                raise ValueError("Passkey response too large.")
            if statement is None:
                result = verify_registration_response(
                    credential=credential,
                    expected_challenge=row["challenge"],
                    expected_rp_id=row["rp_id"],
                    expected_origin=row["origin"],
                    require_user_verification=True,
                )
                self.store.execute(
                    "INSERT INTO device_passkeys VALUES (?,?,?,?,?,?,?)",
                    (
                        bytes_to_base64url(result.credential_id),
                        device_id,
                        result.credential_public_key,
                        result.sign_count,
                        row["origin"],
                        row["rp_id"],
                        self.clock(),
                    ),
                )
            else:
                key = self.store.one(
                    "SELECT * FROM device_passkeys WHERE credential_id=? AND device_id=?",
                    (credential.get("id"), device_id),
                )
                if not key or key["origin"] != row["origin"]:
                    raise ValueError("This passkey does not belong to this phone and origin.")
                result = verify_authentication_response(
                    credential=credential,
                    expected_challenge=row["challenge"],
                    expected_rp_id=row["rp_id"],
                    expected_origin=row["origin"],
                    credential_public_key=key["public_key"],
                    credential_current_sign_count=key["sign_count"],
                    require_user_verification=True,
                )
                self.store.execute(
                    "UPDATE device_passkeys SET sign_count=? WHERE credential_id=?",
                    (result.new_sign_count, key["credential_id"]),
                )
            return {"verified": True}

    def remove(self, device_id, credential_id):
        with self.store.lock:
            self.store.execute(
                "DELETE FROM device_passkeys WHERE credential_id=? AND device_id=?",
                (credential_id, device_id),
            )
            self.store.execute("DELETE FROM passkey_ceremonies WHERE device_id=?", (device_id,))
