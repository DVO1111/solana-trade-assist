import os
import time
import json
import requests
from typing import Optional, List
from datetime import datetime, timezone
from dotenv import load_dotenv

# ===== LOAD ENV =====
local_env_path = os.path.join(os.path.dirname(__file__), ".env")
user_env_path = r"C:\Users\HP\.env"

if os.path.exists(local_env_path):
    load_dotenv(dotenv_path=local_env_path)
    print(f"[ENV] Loaded from {local_env_path}")
elif os.path.exists(user_env_path):
    load_dotenv(dotenv_path=user_env_path)
    print(f"[ENV] Loaded from {user_env_path}")
else:
    print("[ENV] No .env file found in either location — relying on system environment variables.")

# ===== CONFIG =====
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
WATCH_WALLET = os.getenv("WATCH_WALLET")
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "last_sig.json"
POLL_INTERVAL_SEC = 20   # safer for Helius free tier
MAX_BACKOFF = 300        # cap exponential backoff at 5 minutes

# ===== STATE STORAGE =====
def load_last_signature() -> Optional[str]:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return data.get("last_signature")
    except Exception:
        return None

def save_last_signature(sig: str):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_signature": sig}, f)

# ===== HELIUS REST FETCH =====
def fetch_recent_transactions(address: str, before: Optional[str] = None, limit: int = 20) -> List[dict]:
    url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
    if before:
        url += f"&before={before}"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json() or []

# ===== TELEGRAM SEND =====
def send_telegram_text(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Bot token or chat ID missing — skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"[TELEGRAM] Failed: {r.status_code} {r.text}")

# ===== TOKEN METADATA LOOKUP =====
_token_cache = {}

def fetch_token_metadata(mint: str):
    if mint in _token_cache:
        return _token_cache[mint]

    try:
        url = f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_API_KEY}"
        resp = requests.post(url, json={"mintAccounts": [mint]}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and len(data) > 0:
            meta = data[0]
            name = meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {}).get("name")
            symbol = meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {}).get("symbol")
            if name: name = name.strip()
            if symbol: symbol = symbol.strip()
            _token_cache[mint] = (name or "unknown", symbol or "unknown")
            return _token_cache[mint]
    except Exception as e:
        print(f"[META] Failed to fetch metadata for {mint}: {e}")

    _token_cache[mint] = ("unknown", "unknown")
    return _token_cache[mint]

# ===== TOKEN DETAIL EXTRACTION =====
def ts_to_iso(ts_seconds: int) -> str:
    return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).isoformat()

def extract_token_details(tx: dict):
    signature = tx.get("signature")
    timestamp = tx.get("timestamp")
    iso_time = ts_to_iso(timestamp) if timestamp else "unknown"

    token_name = "unknown"
    ticker = "unknown"
    mint = "unknown"

    transfers = tx.get("tokenTransfers") or []
    candidate = None
    for t in transfers:
        if t.get("fromUserAccount") == WATCH_WALLET or t.get("toUserAccount") == WATCH_WALLET:
            candidate = t
            break
    if not candidate and transfers:
        candidate = transfers[0]

    if candidate:
        mint = candidate.get("mint") or mint
        token_info = candidate.get("token") or {}
        token_name = token_info.get("name") or candidate.get("tokenName") or token_name
        ticker = token_info.get("symbol") or candidate.get("tokenSymbol") or ticker

    if mint != "unknown" and (token_name == "unknown" or ticker == "unknown"):
        meta_name, meta_symbol = fetch_token_metadata(mint)
        if token_name == "unknown":
            token_name = meta_name
        if ticker == "unknown":
            ticker = meta_symbol

    return {
        "signature": signature,
        "time": iso_time,
        "token_name": token_name,
        "ticker": ticker,
        "mint": mint
    }

# ===== POLLING LOOP =====
def poll_watch_wallet():
    last_sig = load_last_signature()
    print(f"[INIT] last_seen_signature={last_sig}")

    retry_delay = POLL_INTERVAL_SEC
    last_heartbeat = time.time()

    while True:
        try:
            txs = fetch_recent_transactions(WATCH_WALLET, before=None, limit=20)

            if txs:
                txs_sorted = sorted(txs, key=lambda t: t.get("timestamp", 0))
                if last_sig:
                    new_batch = [t for t in txs_sorted if t.get("signature") != last_sig]
                else:
                    new_batch = txs_sorted

                for tx in new_batch:
                    details = extract_token_details(tx)
                    msg = (
                        f"New activity on <b>{WATCH_WALLET}</b>\n"
                        f"• <b>Token:</b> {details['token_name']} ({details['ticker']})\n"
                        f"• <b>Mint:</b> {details['mint']}\n"
                        f"• <b>Time (UTC):</b> {details['time']}\n"
                        f"• <b>Sig:</b> {details['signature']}"
                    )
                    print(f"[ALERT] {msg.replace(chr(10),' | ')}")
                    send_telegram_text(msg)
                    last_sig = tx.get("signature")
                    save_last_signature(last_sig)

                if not load_last_signature() and txs_sorted:
                    newest_sig = txs_sorted[-1].get("signature")
                    if newest_sig:
                        save_last_signature(newest_sig)
                        last_sig = newest_sig

            # Reset retry delay after success
            retry_delay = POLL_INTERVAL_SEC

            # Heartbeat every 5 minutes
            if time.time() - last_heartbeat > 300:
                print("[HEARTBEAT] Watcher is alive.")
                last_heartbeat = time.time()

            time.sleep(POLL_INTERVAL_SEC)

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"[RATE LIMIT] Too many requests. Backing off for {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_BACKOFF)
            else:
                print(f"[NETWORK] HTTP error: {e}. Retrying in 10s...")
                time.sleep(10)

        except requests.RequestException as e:
            print(f"[NETWORK] {e}. Retrying in 10s...")
            time.sleep(10)

        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(5)

# ===== MAIN =====
if __name__ == "__main__":
    for var in ["HELIUS_API_KEY", "WATCH_WALLET"]:
        if not os.getenv(var):
            raise RuntimeError(f"Missing env var: {var}")
    send_telegram_text("Watcher starting…")
    poll_watch_wallet()
