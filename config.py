import os
import sys
from pathlib import Path

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


def _managed_port(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default, False
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default, False
    return (value, True) if 1 <= value <= 65535 else (default, False)


MANAGED_PORT, _MANAGED_PORT_VALID = _managed_port("PHANTOMLINK_MANAGED_PORT", 5443)
ENROLLMENT_PORT, _ENROLLMENT_PORT_VALID = _managed_port(
    "PHANTOMLINK_ENROLLMENT_PORT", 5444
)
MANAGED_HOST = os.getenv("PHANTOMLINK_MANAGED_HOST", "")
MANAGED_DB = os.getenv("PHANTOMLINK_MANAGED_DB", "")
MANAGED_CA_CERT = os.getenv("PHANTOMLINK_CA_CERT", "")
MANAGED_CA_KEY = os.getenv("PHANTOMLINK_CA_KEY", "")
MANAGED_TLS_CERT = os.getenv("PHANTOMLINK_TLS_CERT", "")
MANAGED_TLS_KEY = os.getenv("PHANTOMLINK_TLS_KEY", "")
MANAGED_STORE = os.getenv("PHANTOMLINK_MANAGED_STORE", "managed-store")


def managed_phase2_configured():
    return any(
        os.getenv(name, "").strip()
        for name in (
            "PHANTOMLINK_MANAGED_HOST",
            "PHANTOMLINK_MANAGED_DB",
            "PHANTOMLINK_CA_CERT",
            "PHANTOMLINK_CA_KEY",
            "PHANTOMLINK_TLS_CERT",
            "PHANTOMLINK_TLS_KEY",
            "PHANTOMLINK_MANAGED_PORT",
            "PHANTOMLINK_ENROLLMENT_PORT",
        )
    )


def managed_phase2_enabled():
    values = (
        MANAGED_HOST,
        MANAGED_DB,
        MANAGED_CA_CERT,
        MANAGED_CA_KEY,
        MANAGED_TLS_CERT,
        MANAGED_TLS_KEY,
        MANAGED_STORE,
    )
    files = (MANAGED_CA_CERT, MANAGED_CA_KEY, MANAGED_TLS_CERT, MANAGED_TLS_KEY)
    return all(isinstance(value, str) and value.strip() for value in values) and all(
        Path(path).is_file() for path in files
    ) and _MANAGED_PORT_VALID and _ENROLLMENT_PORT_VALID

# API Configuration
API_KEY = os.getenv("PHANTOMLINK_API_KEY", "PhantomLink-API-2026")

# Client Configuration
CLIENT_PASSWORD = os.getenv("PHANTOMLINK_PASSWORD", "PhantomLink")
