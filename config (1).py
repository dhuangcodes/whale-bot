import os
from dotenv import load_dotenv
load_dotenv()

DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_URL", "")
MIN_TRADE_USD        = float(os.getenv("MIN_TRADE_USD", "1000"))
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL", "30"))
TOP_WALLETS_COUNT    = int(os.getenv("TOP_WALLETS_COUNT", "300"))  # top wallets to monitor
