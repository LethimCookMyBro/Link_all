"""Edge-case & error-handling tests for PhantomLink.

STRICT RULE honored: C2/Discord behavior is tested with mocks / stubs only.
No real sockets are bound and no external API (Discord, ipify, ...) is ever
contacted. Existing test assertions are untouched.

Covers:
- Discord webhook failure modes (network error, long messages, unset webhook)
- C2 beacon failure modes (socket + ipify + post all down)
- C2 auth handshake (invalid/empty/oversized/malformed/HTTP-probe messages)
- ClientManager duplicate handling
- ConnectionHealth edge cases (no commands, mixed results)
- C2 API key auth (401 vs 200)
- Discord bot gateway failure (empty token, LoginFailure, generic error)
- Client C2 discovery (config SERVER_IP honored)
- Regression guard: no live webhook URL hardcoded in source
"""

import asyncio
import io
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import API_KEY, CLIENT_PASSWORD, SERVER_IP  # noqa: E402

import C2.C2 as c2  # noqa: E402


class DiscordWebhookErrorHandlingTests(unittest.TestCase):
    """discord_logger / discord_send_file must never raise or hit the network
    when the webhook is unset, and must swallow network errors."""

    FAKE_WEBHOOK = "https://example.invalid/hook"

    def test_logger_swallows_network_errors(self):
        with patch.object(c2, "DISCORD_WEBHOOK", self.FAKE_WEBHOOK):
            with patch("C2.C2.requests.post", side_effect=Exception("network down")):
                c2.discord_logger("hello")  # must not raise

    def test_logger_truncates_long_message(self):
        long_msg = "A" * 3000
        with patch.object(c2, "DISCORD_WEBHOOK", self.FAKE_WEBHOOK):
            with patch("C2.C2.requests.post") as mock_post:
                c2.discord_logger(long_msg)
                posted = mock_post.call_args.kwargs["json"]["content"]
                self.assertLessEqual(len(posted), 1900 + len("\n... (truncated)"))
                self.assertIn("(truncated)", posted)

    def test_logger_skips_network_when_webhook_unset(self):
        with patch.object(c2, "DISCORD_WEBHOOK", ""):
            with patch("C2.C2.requests.post") as mock_post:
                c2.discord_logger("hello")
                mock_post.assert_not_called()

    def test_send_file_skips_network_when_webhook_unset(self):
        with patch.object(c2, "DISCORD_WEBHOOK", ""):
            with patch("C2.C2.requests.post") as mock_post:
                c2.discord_send_file("does_not_matter.png")
                mock_post.assert_not_called()

    def test_send_file_swallows_network_errors(self):
        with patch.object(c2, "DISCORD_WEBHOOK", self.FAKE_WEBHOOK):
            with patch("C2.C2.requests.post", side_effect=Exception("boom")):
                with patch("builtins.open", unittest.mock.mock_open(read_data=b"x")):
                    c2.discord_send_file("fake.png", "hi")  # must not raise


class BeaconErrorHandlingTests(unittest.TestCase):
    """broadcast_c2_beacon must survive socket + ipify + webhook failures."""

    def test_beacon_skips_when_webhook_unset(self):
        with patch.object(c2, "DISCORD_WEBHOOK", ""):
            with patch("C2.C2.socket.socket") as mock_sock, \
                 patch("C2.C2.requests.post") as mock_post, \
                 patch("C2.C2.requests.get") as mock_get:
                c2.broadcast_c2_beacon()
                mock_sock.assert_not_called()
                mock_post.assert_not_called()
                mock_get.assert_not_called()

    def test_beacon_survives_total_network_failure(self):
        with patch.object(c2, "DISCORD_WEBHOOK", "https://example.invalid/hook"):
            with patch("C2.C2.socket.socket", side_effect=Exception("no net")), \
                 patch("C2.C2.requests.get", side_effect=Exception("no dns")), \
                 patch("C2.C2.requests.post", side_effect=Exception("no post")):
                c2.broadcast_c2_beacon()  # must not raise

    def test_beacon_posts_lan_ip_when_public_lookup_fails(self):
        sock = MagicMock()
        sock.getsockname.return_value = ("192.168.1.50", 0)
        with patch.object(c2, "DISCORD_WEBHOOK", "https://example.invalid/hook"):
            with patch("C2.C2.requests.post") as mock_post, \
                 patch("C2.C2.requests.get", side_effect=Exception("ipify down")), \
                 patch("C2.C2.socket.socket", return_value=sock):
                c2.broadcast_c2_beacon()
                posted = mock_post.call_args.kwargs["json"]["content"]
                self.assertIn("[PHANTOMLINK_C2_HOST]", posted)
                self.assertIn("192.168.1.50", posted)


class ClientManagerAuthHandshakeTests(unittest.TestCase):
    """add_client: invalid / empty / oversized / HTTP-probe handshakes."""

    def setUp(self):
        self.cm = c2.ClientManager()
        self.conn = MagicMock()
        self.addr = ("10.0.0.5", 5000)

    def test_invalid_password_rejected(self):
        self.cm._recv_message = Mock(side_effect=[b"wrong-password", b"alice"])
        result = self.cm.add_client(self.conn, self.addr)
        self.assertIsNone(result)
        self.conn.close.assert_called()

    def test_empty_handshake_rejected(self):
        self.cm._recv_message = Mock(return_value=None)
        result = self.cm.add_client(self.conn, self.addr)
        self.assertIsNone(result)
        self.conn.close.assert_called()

    def test_valid_credentials_accepted(self):
        with patch("C2.C2.Notify"), patch("C2.C2.requests.post"):
            self.cm._recv_message = Mock(
                side_effect=[CLIENT_PASSWORD.encode(), b"alice"]
            )
            client_id = self.cm.add_client(self.conn, self.addr)
            self.assertIsNotNone(client_id)
            self.assertIn(client_id, self.cm.clients)
            self.assertEqual(self.cm.clients[client_id]["username"], "alice")

    def test_server_uses_configured_client_password(self):
        """Regression guard: server must accept config's CLIENT_PASSWORD,
        not a hardcoded literal."""
        with patch("C2.C2.Notify"), patch("C2.C2.requests.post"):
            self.cm._recv_message = Mock(side_effect=[CLIENT_PASSWORD.encode(), b"bob"])
            self.assertIsNotNone(self.cm.add_client(self.conn, self.addr))

    def test_duplicate_connection_replaces_old(self):
        old_conn = MagicMock()
        with patch("C2.C2.Notify"), patch("C2.C2.requests.post"):
            self.cm._recv_message = Mock(side_effect=[CLIENT_PASSWORD.encode(), b"alice"])
            first_id = self.cm.add_client(old_conn, self.addr)
            self.cm._recv_message = Mock(side_effect=[CLIENT_PASSWORD.encode(), b"alice"])
            second_id = self.cm.add_client(self.conn, self.addr)
            self.assertNotEqual(first_id, second_id)
            self.assertNotIn(first_id, self.cm.clients)
            self.assertIn(second_id, self.cm.clients)
            old_conn.close.assert_called()

    def test_recv_message_rejects_http_probe(self):
        """A client that speaks HTTP instead of the length-prefix protocol is
        silently dropped (the 'GET '/'POST' sniffing guard)."""
        self.cm._recv_exactly = Mock(return_value=b"GET ")
        self.assertIsNone(self.cm._recv_message(self.conn))

    def test_recv_message_rejects_oversized_payload(self):
        self.cm._recv_exactly = Mock(return_value=struct.pack("!I", 11 * 1024 * 1024))
        self.assertIsNone(self.cm._recv_message(self.conn))

    def test_recv_message_rejects_malformed_length(self):
        self.cm._recv_exactly = Mock(return_value=b"\xff\xff")
        self.assertIsNone(self.cm._recv_message(self.conn))

    def test_recv_message_handles_socket_timeout(self):
        def raise_timeout(_conn, _n):
            raise __import__("socket").timeout("timed out")

        self.cm._recv_exactly = Mock(side_effect=raise_timeout)
        self.assertIsNone(self.cm._recv_message(self.conn))

    def test_recv_exactly_returns_none_on_disconnect(self):
        self.cm._recv_exactly = Mock(return_value=b"")
        self.assertIsNone(self.cm._recv_message(self.conn))


class ConnectionHealthEdgeTests(unittest.TestCase):
    def test_no_commands_no_division_error(self):
        ch = c2.ConnectionHealth()
        stats = ch.get_stats()
        self.assertEqual(stats["total_commands"], 0)
        self.assertEqual(ch.connection_quality, 100)

    def test_mixed_results_stats(self):
        ch = c2.ConnectionHealth()
        ch.record_command(True, 0.2)
        ch.record_command(True, 0.4)
        ch.record_command(False, 1.0)
        stats = ch.get_stats()
        self.assertEqual(stats["total_commands"], 3)
        self.assertIn("%", stats["success_rate"])
        self.assertIn("%", stats["quality"])

    def test_keepalive_failure_accumulates_and_resets(self):
        self.cm = c2.ClientManager()
        with patch("C2.C2.Notify"), patch("C2.C2.requests.post"):
            self.cm._recv_message = Mock(side_effect=[CLIENT_PASSWORD.encode(), b"u"])
            cid = self.cm.add_client(MagicMock(), ("1.1.1.1", 5000))
            self.assertEqual(self.cm.increment_keepalive_failure(cid), 1)
            self.assertEqual(self.cm.increment_keepalive_failure(cid), 2)
            self.cm.update_last_seen(cid)
            self.assertEqual(self.cm.clients[cid]["keepalive_failures"], 0)


class C2ApiAuthTests(unittest.TestCase):
    def _make_handler(self, api_key):
        handler = c2.C2APIHandler.__new__(c2.C2APIHandler)
        handler.headers = {"X-API-Key": api_key}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_wrong_api_key_returns_401(self):
        handler = self._make_handler("wrong-key")
        self.assertFalse(handler._check_auth())
        self.assertEqual(handler.send_response.call_args[0][0], 401)
        body = handler.wfile.getvalue()
        self.assertIn(b"Unauthorized", body)

    def test_correct_api_key_passes(self):
        handler = self._make_handler(API_KEY)
        self.assertTrue(handler._check_auth())
        handler.send_response.assert_not_called()


class DiscordBotGatewayTests(unittest.TestCase):
    """discord_bot.main() must not start or hang on gateway problems."""

    def test_empty_token_skips_start(self):
        import discord_bot

        with patch("config.DISCORD_BOT_TOKEN", ""):
            with patch("discord_bot.bot.start") as mock_start:
                asyncio.run(discord_bot.main())
                mock_start.assert_not_called()

    def test_login_failure_swallowed(self):
        import discord

        import discord_bot

        with patch("config.DISCORD_BOT_TOKEN", "fake-token"):
            with patch("discord_bot.bot.start", side_effect=discord.LoginFailure("nope")):
                asyncio.run(discord_bot.main())  # must not raise

    def test_generic_gateway_error_swallowed(self):
        import discord_bot

        with patch("config.DISCORD_BOT_TOKEN", "fake-token"):
            with patch("discord_bot.bot.start", side_effect=RuntimeError("rate limited")):
                asyncio.run(discord_bot.main())  # must not raise


class ClientDiscoveryTests(unittest.TestCase):
    def test_discover_uses_configured_server_ip_first(self):
        import client.PhantomLink as phantom

        with patch("client.PhantomLink.socket.socket") as mock_sock:
            mock_sock.return_value.connect_ex.return_value = 0  # port open
            found = phantom.discover_c2_server(port=5000)
        self.assertEqual(found, SERVER_IP)


class NoHardcodedWebhookGuardTests(unittest.TestCase):
    """Regression guard: a live webhook URL must never return to the source."""

    def test_no_live_webhook_url_in_source(self):
        pattern = re.compile(r"https://discord\.com/api/webhooks/\d+/")
        for rel in ("config.py", "client/PhantomLink.py", "C2/C2.py"):
            content = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertFalse(
                pattern.search(content),
                f"live webhook URL found in {rel}",
            )

    def test_client_fallback_password_matches_config(self):
        import client.PhantomLink as phantom

        self.assertEqual(phantom.CLIENT_PASSWORD, CLIENT_PASSWORD)


if __name__ == "__main__":
    unittest.main()
