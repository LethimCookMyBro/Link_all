"""Shared legacy payload encryption primitives."""

from client.transport import decrypt, derive_key, encrypt

__all__ = ["derive_key", "encrypt", "decrypt"]
