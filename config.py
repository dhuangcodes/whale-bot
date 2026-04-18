import os
from dotenv import load_dotenv
load_dotenv()

DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_URL", "")
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
MIN_TRADE_USD        = float(os.getenv("MIN_TRADE_USD", "1000"))
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL", "45"))
TOP_WALLETS_COUNT    = int(os.getenv("TOP_WALLETS_COUNT", "300"))
