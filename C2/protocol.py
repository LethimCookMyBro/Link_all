"""Wire protocol for the C2 transport layer.

Extracted from ``ClientManager`` (formerly inline in ``C2.C2``) following the
Sliver-style separation of *transport* from *logic*: this module owns the
byte-level framing (4-byte big-endian length prefix + payload), the HTTP
probe rejection, the 10MB size cap, and the exact-read loop. ``C2.C2`` keeps
the session/state logic and simply delegates its socket framing here.

Protocol (unchanged from the original implementation):

* ``encode_message`` -> ``struct.pack('!I', len)`` followed by the payload.
* ``decode_message`` reads the 4-byte header with ``recv_exactly``; rejects
  HTTP probes (``GET``/``POST``/``HTTP``/``HEAD``), malformed headers, and
  messages over ``MAX_MESSAGE_SIZE`` by returning ``None`` (silent close).
* All framing failures return ``None`` and never raise — identical to the
  original defensive behaviour.
"""

from __future__ import annotations

import socket
import struct
from typing import Callable, Optional, Tuple

MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB cap, unchanged from original

_HTTP_PREFIXES = (b"GET ", b"POST", b"HTTP", b"HEAD")


# --- encode ----------------------------------------------------------------
def pack_length(message_len: int) -> bytes:
    """4-byte big-endian length prefix."""
    return struct.pack("!I", message_len)


def encode_message(data) -> Tuple[bytes, bytes]:
    """Split a message into (length_packet, payload) for transmission.

    ``data`` may be str (UTF-8 encoded here) or bytes. The caller sends the
    two packets in order, matching the original two-``sendall`` behaviour.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return pack_length(len(data)), data


# --- decode ----------------------------------------------------------------
def is_http_probe(raw_header: bytes) -> bool:
    """True if the leading bytes look like an HTTP request line."""
    return any(raw_header.startswith(p) for p in _HTTP_PREFIXES)


def parse_length_header(raw_header: bytes) -> Optional[int]:
    """Unpack the length header; None if it is an HTTP probe or malformed."""
    if not raw_header or is_http_probe(raw_header):
        return None
    try:
        return struct.unpack("!I", raw_header)[0]
    except struct.error:
        return None


def decode_message(
    recv_exactly: Callable[[int], Optional[bytes]],
    max_size: int = MAX_MESSAGE_SIZE,
) -> Optional[bytes]:
    """Receive one framed message.

    ``recv_exactly(n)`` must return exactly ``n`` bytes or ``None`` on
    timeout/disconnect (see ``recv_exactly`` below). Returns the payload, or
    ``None`` for probes, oversized messages, malformed headers or socket
    errors — mirroring the original silent-close behaviour.
    """
    try:
        raw_header = recv_exactly(4)
        if not raw_header:
            return None
        message_len = parse_length_header(raw_header)
        if message_len is None:
            return None
        if message_len > max_size:
            return None  # silent close on oversized message
        return recv_exactly(message_len)
    except Exception:
        return None


def recv_exactly(conn, n: int) -> Optional[bytes]:
    """Read exactly ``n`` bytes; None on timeout, disconnect or error."""
    data = b""
    while len(data) < n:
        try:
            packet = conn.recv(n - len(data))
            if not packet:
                return None
            data += packet
        except socket.timeout:
            return None
        except Exception:
            return None
    return data
