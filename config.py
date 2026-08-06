import os

# Discord Configuration
DISCORD_WEBHOOK = os.getenv("PHANTOMLINK_WEBHOOK", "https://discord.com/api/webhooks/1525081864094613615/4DkAojzJaoqsbolWR2E59IVwWeZY21CVr4-eNcnvXWB2nAKad4wpQ3mZVddNnNlw8pV7")
DISCORD_BOT_TOKEN = os.getenv("PHANTOMLINK_BOT_TOKEN", "")

# Server Configuration  
SERVER_IP = os.getenv("PHANTOMLINK_SERVER_IP", "81.10.55.8")
SERVER_HOST = os.getenv("PHANTOMLINK_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PHANTOMLINK_PORT", "5000"))

# API Configuration
API_KEY = os.getenv("PHANTOMLINK_API_KEY", "PhantomLink-API-2026")

# Client Configuration
CLIENT_PASSWORD = os.getenv("PHANTOMLINK_PASSWORD", "PhantomLink")
