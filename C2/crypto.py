"""Payload encryption for the C2 client command channel.

Every payload on the raw TCP channel (handshake credentials, ``CMD:``
commands, command output, keepalive) is wrapped with PyNaCl's
``SecretBox`` (XSalsa20-Poly1305) before the existing length-prefix
framing, and unwrapped after it on the receiving side.

The shared key is derived from ``PHANTOMLINK_PASSWORD`` (the same value
both sides already use for the handshake), so the password itself never
crosses the wire anymore.

Only the payload is encrypted — the 4-byte length header stays plaintext
so ``protocol.py`` framing (HTTP-probe rejection, size cap, exact read)
keeps working unchanged. Decryption failure (wrong key, tampering,
garbage) returns ``None``, so both peers fall into their existing
defensive "silent close" path — a corrupted message can never be
interpreted as a command or credential.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

# Domain-separation constant: binds the derived key to this channel so the
# same password can never produce an interoperable key in another context.
_DOMAIN = b"phantomlink-c2-v1"


def derive_key(password: str) -> bytes:
    """Derive the 32-byte channel key from the shared password."""
    return hashlib.sha256(_DOMAIN + password.encode("utf-8")).digest()


def encrypt(key: bytes, data) -> bytes:
    """Encrypt str or bytes; returns nonce + ciphertext + tag as one blob."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return SecretBox(key).encrypt(data)


def decrypt(key: bytes, data) -> Optional[bytes]:
    """Decrypt a blob produced by :func:`encrypt`.

    Returns ``None`` for empty input, wrong keys, tampered ciphertext or
    any other failure — never raises.
    """
    if not data:
        return None
    try:
        return SecretBox(key).decrypt(data)
    except (CryptoError, TypeError, ValueError):
        return None
