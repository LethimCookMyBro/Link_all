import os

# Discord Configuration
DISCORD_WEBHOOK = os.getenv("PHANTOMLINK_WEBHOOK", "***REMOVED***")
DISCORD_BOT_TOKEN = os.getenv("PHANTOMLINK_BOT_TOKEN", "")

# Server Configuration  
SERVER_IP = os.getenv("PHANTOMLINK_SERVER_IP", "81.10.55.8")
SERVER_HOST = os.getenv("PHANTOMLINK_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PHANTOMLINK_PORT", "5000"))

# API Configuration
API_KEY = os.getenv("PHANTOMLINK_API_KEY", "PhantomLink-API-2026")

# Client Configuration
CLIENT_PASSWORD = os.getenv("PHANTOMLINK_PASSWORD", "PhantomLink")
