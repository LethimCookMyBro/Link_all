"""Encrypted command channel tests (fully offline — no ports, no network).

Covers the PyNaCl SecretBox payload encryption added to both peers:

* round-trip integrity (text + binary, including 1MB payloads)
* tamper detection (a flipped bit must yield None, never corrupt data)
* wrong-key / garbage rejection
* wire secrecy (password, username and commands never appear in plaintext)
* a real end-to-end handshake + command round-trip over ``socket.socketpair``
  (an in-memory pipe — no port is bound)
"""

import os
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from C2.crypto import decrypt as server_decrypt
from C2.crypto import derive_key as server_derive_key
from C2.crypto import encrypt as server_encrypt
from client.PhantomLink import (
    CLIENT_PASSWORD,
    ShellClient,
    _decrypt as client_decrypt,
    _derive_key as client_derive_key,
    _encrypt as client_encrypt,
)


class RecordingSocket:
    """In-memory socket stand-in that records everything sent."""

    def __init__(self):
        self.sent = b""
        self.inbox = b""

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        if not self.inbox:
            return b""
        chunk = self.inbox[:n]
        self.inbox = self.inbox[n:]
        return chunk

    def settimeout(self, timeout):
        pass

    def close(self):
        pass


class TestCryptoRoundTrip(unittest.TestCase):
    def test_server_round_trip_text_and_binary(self):
        key = server_derive_key("pw")
        for size in (0, 1, 4095, 4096, 65536, 1024 * 1024):
            payload = os.urandom(size)
            blob = server_encrypt(key, payload)
            self.assertNotEqual(blob, payload)  # actually encrypted
            self.assertEqual(server_decrypt(key, blob), payload)  # byte-identical back
        self.assertEqual(server_decrypt(key, server_encrypt(key, "hello")), b"hello")

    def test_client_and_server_crypto_interoperate(self):
        # Same password must produce the same key on both peers...
        self.assertEqual(server_derive_key("pw"), client_derive_key("pw"))
        # ...and a blob produced by one side decrypts on the other.
        blob = client_encrypt(client_derive_key("pw"), b"CMD:whoami")
        self.assertEqual(server_decrypt(server_derive_key("pw"), blob), b"CMD:whoami")
        blob = server_encrypt(server_derive_key("pw"), b"admin")
        self.assertEqual(client_decrypt(client_derive_key("pw"), blob), b"admin")


class TestCryptoTamperAndKeyMismatch(unittest.TestCase):
    def test_bit_flip_returns_none_never_corrupt_data(self):
        key = server_derive_key("pw")
        blob = bytearray(server_encrypt(key, b"CMD:whoami"))
        blob[-1] ^= 0x01  # flip one bit inside the Poly1305 tag
        self.assertIsNone(server_decrypt(key, bytes(blob)))

    def test_wrong_key_returns_none(self):
        blob = server_encrypt(server_derive_key("right"), b"data")
        self.assertIsNone(server_decrypt(server_derive_key("wrong"), blob))

    def test_empty_and_garbage_input_return_none(self):
        key = server_derive_key("pw")
        self.assertIsNone(server_decrypt(key, b""))
        self.assertIsNone(server_decrypt(key, b"not-ciphertext"))


class TestWireSecrecy(unittest.TestCase):
    """The plaintext values must never appear in the bytes on the wire."""

    def test_server_command_plaintext_never_on_wire(self):
        import C2.C2 as c2mod

        cm = c2mod.ClientManager()
        recorder = RecordingSocket()
        self.assertTrue(cm._send_message(recorder, "CMD:whoami"))
        self.assertNotIn(b"whoami", recorder.sent)
        self.assertNotIn(b"CMD:", recorder.sent)
        self.assertGreater(len(recorder.sent), len("CMD:whoami"))  # nonce+tag overhead

    def test_client_handshake_password_and_username_never_on_wire(self):
        sc = ShellClient()
        sc.socket = RecordingSocket()
        self.assertTrue(sc._send_message(CLIENT_PASSWORD))  # handshake password
        self.assertTrue(sc._send_message("alice"))  # handshake username
        self.assertNotIn(CLIENT_PASSWORD.encode(), sc.socket.sent)
        self.assertNotIn(b"alice", sc.socket.sent)


class TestClientRecvDecryptPath(unittest.TestCase):
    def test_recv_decrypt_failure_returns_none(self):
        sc = ShellClient()
        sock = RecordingSocket()
        garbage = b"G" * 50
        sock.inbox = struct.pack("!I", len(garbage)) + garbage
        sc.socket = sock
        self.assertIsNone(sc._recv_message())


class TestEncryptedChannelIntegration(unittest.TestCase):
    """Real handshake + command round-trip over an in-memory socket pair."""

    def _run_handshake(self, password):
        import C2.C2 as c2mod

        sock_server, sock_client = socket.socketpair()
        cm = c2mod.ClientManager()
        result = {}

        def server_add_client():
            with patch.object(c2mod, "Notify"), patch("C2.C2.requests.post"):
                result["client_id"] = cm.add_client(sock_server, ("127.0.0.1", 5000))

        t = threading.Thread(target=server_add_client)
        t.start()

        sc = ShellClient()
        sc.socket = sock_client
        sc.username = "alice"
        sc._send_message(password)
        sc._send_message("alice")

        t.join(timeout=5)
        return cm, sc, sock_server, sock_client, result

    def test_full_handshake_and_command_round_trip(self):
        cm, sc, sock_server, sock_client, result = self._run_handshake(CLIENT_PASSWORD)
        client_id = result.get("client_id")
        self.assertIsNotNone(client_id)
        self.assertEqual(cm.clients[client_id]["username"], "alice")

        # server -> client command, then client -> server output
        self.assertTrue(cm._send_message(sock_server, "CMD:whoami"))
        self.assertEqual(sc._recv_message(), b"CMD:whoami")
        self.assertTrue(sc._send_message("admin"))
        self.assertEqual(cm._recv_message(sock_server), b"admin")

        sock_server.close()
        sock_client.close()

    def test_wrong_password_handshake_rejected(self):
        import C2.C2 as c2mod

        cm, sc, sock_server, sock_client, result = self._run_handshake("WRONG-PASSWORD")
        self.assertIsNone(result.get("client_id"))
        self.assertEqual(cm.clients, {})
        sock_server.close()
        sock_client.close()


if __name__ == "__main__":
    unittest.main()
