"""
Run this on the server to get a current game summary:
  python3 get_summary.py            # all games
  python3 get_summary.py knicks     # specific game
  python3 get_summary.py --post     # post to Discord summary channel
"""
import sys
import os
import json
import pickle

# Load the summary store from the running bot's pickled state
# The bot saves it to /tmp/summary_store.pkl every cycle
STORE_FILE = "/tmp/summary_store.pkl"
WEBHOOK    = os.getenv("WEBHOOK_SUMMARY", os.getenv("WEBHOOK_NBA", ""))

def main():
    if not os.path.exists(STORE_FILE):
        print("No summary data yet — bot may not be running or no alerts fired today.")
        return

    with open(STORE_FILE, "rb") as f:
        store = pickle.load(f)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    post = "--post" in sys.argv

    if args:
        query = " ".join(args).lower()
        games = store.get_all_games()
        match = next((g for g in games if query in g.lower()), None)
        if match:
            text = store.get_summary(match)
        else:
            text = f"No data for '{query}'.\nActive games: {', '.join(games) or 'none'}"
    else:
        text = store.get_all_summaries_text()

    print(text)

    if post and WEBHOOK:
        import requests
        chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK, json={"content": f"```\n{chunk}\n```"}, timeout=5)
        print("\n→ Posted to Discord")

if __name__ == "__main__":
    main()
