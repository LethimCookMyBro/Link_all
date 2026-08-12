import os
import sys

# Automatically load .env file from working directory, exe directory, or script directory
_possible_env_paths = [
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.path.dirname(sys.executable), ".env"),
    os.path.join(os.path.dirname(os.path.dirname(sys.executable)), ".env"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
]

for _env_candidate in _possible_env_paths:
    if os.path.exists(_env_candidate):
        try:
            from dotenv import load_dotenv

            load_dotenv(_env_candidate, override=True)
        except ImportError:
            with open(_env_candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        os.environ[key.strip()] = val.strip().strip("'\"")
        break

# Discord Configuration
# No default webhook: a live Discord webhook URL is a secret (its token is
# embedded in the URL). Operators must supply PHANTOMLINK_WEBHOOK via .env.
DISCORD_WEBHOOK = os.getenv("PHANTOMLINK_WEBHOOK", "")
DISCORD_BOT_TOKEN = os.getenv("PHANTOMLINK_BOT_TOKEN", "")

# Server Configuration
SERVER_IP = os.getenv("PHANTOMLINK_SERVER_IP", "202.28.78.213")
SERVER_HOST = os.getenv("PHANTOMLINK_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PHANTOMLINK_PORT", "5000"))
MANAGED_PORT = int(os.getenv("PHANTOMLINK_MANAGED_PORT", "5443"))
ENROLLMENT_PORT = int(os.getenv("PHANTOMLINK_ENROLLMENT_PORT", "5444"))
MANAGED_TLS_CERT = os.getenv("PHANTOMLINK_TLS_CERT", "")
MANAGED_TLS_KEY = os.getenv("PHANTOMLINK_TLS_KEY", "")
MANAGED_STORE = os.getenv("PHANTOMLINK_MANAGED_STORE", "managed-store")

# API Configuration
API_KEY = os.getenv("PHANTOMLINK_API_KEY", "PhantomLink-API-2026")

# Client Configuration
CLIENT_PASSWORD = os.getenv("PHANTOMLINK_PASSWORD", "PhantomLink")
