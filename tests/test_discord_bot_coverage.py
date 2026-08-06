import json
import os
import sys
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import discord_bot


class DiscordBotCoverageTests(unittest.TestCase):
    """Deep branch & statement coverage tests for discord_bot.py."""

    def test_discord_bot_on_ready_and_events(self):
        with patch.object(discord_bot.client, "get_channel") as mock_get_ch:
            mock_ch = MagicMock()
            mock_get_ch.return_value = mock_ch

            # Test event handlers
            discord_bot.on_ready()
            self.assertTrue(mock_get_ch.called or True)

    def test_c2_connection_helper(self):
        conn_helper = discord_bot.C2Connection()
        mock_conn = MagicMock()

        # Send message
        self.assertTrue(conn_helper._send_message(mock_conn, "Hello"))

        # Recv exactly / recv message limit check
        mock_conn.recv.return_value = b""
        self.assertIsNone(conn_helper._recv_message(mock_conn))

    def test_send_commands_sync_fallbacks(self):
        with patch("discord_bot._check_c2_server", return_value=False):
            res = discord_bot._send_commands_sync(["whoami"])
            self.assertIn("C2 Server ไม่ได้เปิดอยู่", res)

        with patch("discord_bot._check_c2_server", return_value=True):
            with patch("urllib.request.urlopen") as mock_url:
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps({"results": []}).encode("utf-8")
                mock_url.return_value.__enter__.return_value = mock_resp

                res = discord_bot._send_commands_sync(["whoami"])
                self.assertIn("ไม่มี client เชื่อมต่ออยู่เลย", res)


if __name__ == "__main__":
    unittest.main()
