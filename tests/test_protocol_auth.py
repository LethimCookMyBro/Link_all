"""Tests for the extracted transport (C2/protocol.py) and auth (C2/auth.py)
modules. All framing tests use an in-memory buffer — no real sockets.
"""
import socket
import struct
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "C2")

from auth import check_api_key, check_client_password  # noqa: E402
from protocol import (  # noqa: E402
    MAX_MESSAGE_SIZE,
    decode_message,
    encode_message,
    is_http_probe,
    pack_length,
    parse_length_header,
    recv_exactly,
)


class TestEncoding:
    def test_encode_str_returns_length_and_utf8_payload(self):
        length, payload = encode_message("hello")
        assert length == pack_length(5)
        assert payload == b"hello"

    def test_encode_bytes_passthrough(self):
        length, payload = encode_message(b"\x00\x01raw")
        assert length == pack_length(5)
        assert payload == b"\x00\x01raw"

    def test_pack_length_roundtrip(self):
        for n in (0, 1, 255, 65536, MAX_MESSAGE_SIZE):
            assert struct.unpack("!I", pack_length(n))[0] == n

    def test_roundtrip_encode_decode(self):
        length, payload = encode_message("PhantomLink")
        buf = iter([length, payload])

        def recv(n):
            chunk = b""
            while len(chunk) < n:
                try:
                    chunk += next(buf)
                except StopIteration:
                    return chunk or None
            return chunk

        assert decode_message(recv) == b"PhantomLink"


class TestDecoding:
    def test_http_probe_detection(self):
        assert is_http_probe(b"GET / HTTP/1.1")
        assert is_http_probe(b"POST /")
        assert is_http_probe(b"HTTP/1.1")
        assert is_http_probe(b"HEAD /")
        assert not is_http_probe(b"\x00\x00\x00\x05")

    def test_parse_header_rejects_http_and_garbage(self):
        assert parse_length_header(b"GET ") is None
        assert parse_length_header(b"AB") is None
        assert parse_length_header(b"") is None
        assert parse_length_header(b"\x00\x00\x00\x05") == 5

    def test_empty_header_returns_none(self):
        assert decode_message(lambda n: None) is None

    def test_http_probe_returns_none(self):
        assert decode_message(lambda n: b"GET ") is None

    def test_malformed_header_returns_none(self):
        assert decode_message(lambda n: b"AB") is None

    def test_oversized_message_returns_none(self):
        header = struct.pack("!I", MAX_MESSAGE_SIZE + 1)

        def recv(n):
            return header if n == 4 else b"x"

        assert decode_message(recv) is None

    def test_max_size_exactly_at_limit_passes(self):
        header = struct.pack("!I", MAX_MESSAGE_SIZE)

        def recv(n):
            return header if n == 4 else b"y"

        assert decode_message(recv) == b"y"

    def test_decode_never_raises(self):
        def boom(n):
            raise RuntimeError("socket died")

        assert decode_message(boom) is None


class TestRecvExactly:
    def test_accumulates_partial_reads(self):
        conn = MagicMock()
        conn.recv.side_effect = [b"hel", b"lo"]
        assert recv_exactly(conn, 5) == b"hello"

    def test_disconnect_returns_none(self):
        conn = MagicMock()
        conn.recv.return_value = b""
        assert recv_exactly(conn, 4) is None

    def test_timeout_returns_none(self):
        conn = MagicMock()
        conn.recv.side_effect = socket.timeout("timed out")
        assert recv_exactly(conn, 4) is None

    def test_other_error_returns_none(self):
        conn = MagicMock()
        conn.recv.side_effect = OSError("reset")
        assert recv_exactly(conn, 4) is None


class TestAuth:
    def test_api_key_match(self):
        assert check_api_key("PhantomLink-API-2026", "PhantomLink-API-2026") is True

    def test_api_key_mismatch(self):
        assert check_api_key("wrong", "PhantomLink-API-2026") is False

    def test_api_key_empty_expected_fails_closed(self):
        assert check_api_key("anything", "") is False

    def test_client_password_match(self):
        assert check_client_password("PhantomLink", "PhantomLink") is True

    def test_client_password_mismatch(self):
        assert check_client_password("WrongPassword", "PhantomLink") is False

    def test_client_password_empty_expected_fails_closed(self):
        assert check_client_password("PhantomLink", "") is False

    def test_non_string_inputs_never_raise(self):
        assert check_api_key(None, "key") is False
        assert check_client_password(b"bytes", "PhantomLink") is False
