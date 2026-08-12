"""Side-effect-free framing, JSON, authentication, and legacy crypto helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

MAX_FRAME_SIZE = 10 * 1024 * 1024
MAX_JSON_PAYLOAD_SIZE = 64 * 1024
_DOMAIN = b"phantomlink-c2-v1"


def encode_message(data) -> tuple[bytes, bytes]:
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return len(payload).to_bytes(4, "big"), payload


def encode_json_payload(mapping) -> bytes:
    if not isinstance(mapping, Mapping):
        raise TypeError("JSON payload must be a mapping")
    return json.dumps(
        mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def decode_json_payload(payload, max_size=MAX_JSON_PAYLOAD_SIZE) -> dict:
    payload = bytes(payload)
    if len(payload) > max_size:
        raise ValueError("JSON payload too large")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON payload must be a JSON object")
    return value


def derive_key(password: str) -> bytes:
    """Derive the legacy 32-byte channel key from the shared password."""
    return hashlib.sha256(_DOMAIN + password.encode("utf-8")).digest()


def encrypt(key: bytes, data) -> bytes:
    """Encrypt str or bytes; return nonce, ciphertext, and tag as one blob."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return SecretBox(key).encrypt(data)


def decrypt(key: bytes, data) -> bytes | None:
    """Return decrypted bytes, or None for empty, invalid, or tampered input."""
    if not data:
        return None
    try:
        return SecretBox(key).decrypt(data)
    except (CryptoError, TypeError, ValueError):
        return None


def _auth_field(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, int):
        return str(value).encode("ascii")
    raise TypeError("auth fields must be int, str, or bytes")


def canonical_auth_input(version, agent_id, key_id, nonce) -> bytes:
    fields = (_DOMAIN, version, agent_id, key_id, nonce)
    encoded = (_auth_field(field) for field in fields)
    return b"".join(len(field).to_bytes(4, "big") + field for field in encoded)


def build_proof(secret, version, agent_id, key_id, nonce) -> bytes:
    message = canonical_auth_input(version, agent_id, key_id, nonce)
    return hmac.digest(secret, message, "sha256")


def verify_proof(secret, version, agent_id, key_id, nonce, proof) -> bool:
    try:
        expected = build_proof(secret, version, agent_id, key_id, nonce)
        return hmac.compare_digest(expected, proof)
    except (TypeError, ValueError):
        return False


class FrameDecoder:
    def __init__(self, max_size=MAX_FRAME_SIZE):
        self.max_size = max_size
        self.buffer = bytearray()
        self.expected = None

    def feed(self, chunk) -> list[bytes]:
        self.buffer.extend(chunk)
        frames = []
        while True:
            if self.expected is None:
                if len(self.buffer) < 4:
                    return frames
                self.expected = int.from_bytes(self.buffer[:4], "big")
                del self.buffer[:4]
                if self.expected > self.max_size:
                    raise ValueError("frame too large")
            if len(self.buffer) < self.expected:
                return frames
            frames.append(bytes(self.buffer[: self.expected]))
            del self.buffer[: self.expected]
            self.expected = None
