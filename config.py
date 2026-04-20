import os
from dotenv import load_dotenv
load_dotenv()

WEBHOOK_NBA         = os.getenv("WEBHOOK_NBA", "")
WEBHOOK_MLB         = os.getenv("WEBHOOK_MLB", "")
WEBHOOK_TENNIS      = os.getenv("WEBHOOK_TENNIS", "")
WEBHOOK_VIDEOGAMES  = os.getenv("WEBHOOK_VIDEOGAMES", "")
WEBHOOK_OTHER       = os.getenv("WEBHOOK_OTHER", "")

MIN_TRADE_USD       = float(os.getenv("MIN_TRADE_USD", "3000"))
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", "45"))
TOP_WALLETS_COUNT   = int(os.getenv("TOP_WALLETS_COUNT", "300"))
