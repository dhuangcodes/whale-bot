import os
from dotenv import load_dotenv
load_dotenv()

WEBHOOK_NBA         = os.getenv("WEBHOOK_NBA", "")
WEBHOOK_WORLDCUP    = os.getenv("WEBHOOK_WORLDCUP", "")

MIN_TRADE_USD       = float(os.getenv("MIN_TRADE_USD", "3000"))
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", "45"))
TOP_WALLETS_COUNT   = int(os.getenv("TOP_WALLETS_COUNT", "300"))
