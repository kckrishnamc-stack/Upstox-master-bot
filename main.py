#!/usr/bin/env python3
"""
Upstox 24x7 Master Bot (Cloud-Ready, manual daily token)

Features
--------
- NIFTY + BANKNIFTY indices
- Market Profile from intraday 1m candles:
    * POC, VAH, VAL
- TSB  = Trapped Sellers Buy  (price re-enters value from below VAL)
- TBS  = Trapped Buyers Sell  (price re-enters value from above VAH)
- Initiative Buying  = breaks above VAH from inside
- Initiative Selling = breaks below VAL from inside
- Options:
    * Auto ATM from index LTP
    * ATM ± 3 strikes (CE + PE) via /v2/option/contract
- HFT footprints on options:
    * Fast tick
    * Volume spike vs baseline
    * Small price shock
- Gamma blast:
    * Large move from base price after HFT
- Telegram alerts for all signals

Dependencies:
    pip install requests

Configuration:
    All sensitive values are taken from environment variables:

    ACCESS_TOKEN     = Upstox access token (daily)
    BOT_TOKEN        = Telegram bot token
    CHAT_ID          = Telegram chat id
    NIFTY_EXPIRY     = Nifty weekly expiry, e.g. 2025-11-25
    BANKNIFTY_EXPIRY = BankNifty expiry, e.g. 2025-11-25
"""

import os
import time
from datetime import datetime, timedelta
import requests

# =====================================================
#                  ENV CONFIG
# =====================================================

ACCESS_TOKEN     = os.environ.get("ACCESS_TOKEN", "")
NIFTY_EXPIRY     = os.environ.get("NIFTY_EXPIRY", "2025-11-25")
BANKNIFTY_EXPIRY = os.environ.get("BANKNIFTY_EXPIRY", "2025-11-25")
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
CHAT_ID          = os.environ.get("CHAT_ID", "")

# General parameters
PRICE_BUCKET_SIZE       = 10.0     # MP bin size for indices
MP_REFRESH_MINUTES      = 15       # how often to rebuild MP
POLL_INTERVAL_SEC       = 1.0      # main loop sleep

# HFT / Gamma parameters
MIN_TICK_INTERVAL_SEC   = 0.35
HFT_VOLUME_MULTIPLIER   = 3.0
PRICE_MOVE_SMALL_PCT    = 0.20
PRICE_MOVE_GAMMA_PCT    = 1.00
LOOKBACK_TICKS_FOR_BASE = 25
RECENT_HFT_SEC          = 30
ALERT_COOLDOWN_SEC      = 90

# How many strikes on either side of ATM
STRIKES_EACH_SIDE       = 3

# API constants
BASE_URL   = "https://api.upstox.com/v2"
NIFTY_KEY  = "NSE_INDEX|Nifty 50"
BANK_KEY   = "NSE_INDEX|Nifty Bank"

QUOTE_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}
CANDLE_HEADERS = {
    "Accept": "application/json",
}
OPTION_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

# =====================================================
#                   TELEGRAM
# =====================================================

def send_telegram(msg: str):
    """
    Send message to Telegram; if no bot/chat configured, just prints.
    """
    text = str(msg)
    if not BOT_TOKEN or not CHAT_ID:
        print("TG:", text)
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text}
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print("Telegram error:", e)

# =====================================================
#       HISTORICAL CANDLES (for Market Profile)
# =====================================================

def fetch_intraday_candles(instrument_key: str, interval: str = "1minute"):
    """
    Uses v2 intraday candle endpoint.
    Response: data.candles = [[ts, o, h, l, c, v], ...]
    """
    url = f"{BASE_URL}/historical-candle/intraday/{instrument_key}/{interval}"
    try:
        r = requests.get(url, headers=CANDLE_HEADERS, timeout=10)
        js = r.json()
        if js.get("status") != "success":
            print("Intraday error:", instrument_key, js)
            return []
        return js["data"]["candles"]
    except Exception as e:
        print("Intraday exception:", instrument_key, e)
        return []

def build_market_profile(candles, bucket_size: float):
    """
    Simple volume-at-price MP using close price bucket.
    Returns dict with POC, VAH, VAL, Day High/Low.
    """
    if not candles:
        return None

    vol_at_price = {}
    total_vol = 0
    d_high = float("-inf")
    d_low  = float("inf")

    for c in candles:
        if len(c) < 6:
            continue
        _ts, o, h, l, close, vol = c
        if close is None:
            continue

        bucket = round(close / bucket_size) * bucket_size
        vol_at_price[bucket] = vol_at_price.get(bucket, 0) + vol
        total_vol += vol

        if h is not None:
            d_high = max(d_high, h)
        if l is not None:
            d_low = min(d_low, l)

    if not vol_at_price or total_vol <= 0:
        return None

    # POC
    poc_price = max(vol_at_price, key=lambda p: vol_at_price[p])

    # 70% Value Area
    sorted_by_vol = sorted(vol_at_price.items(), key=lambda x: x[1], reverse=True)
    target = total_vol * 0.7
    cum = 0
    va_prices = set()
    for price, v in sorted_by_vol:
        cum += v
        va_prices.add(price)
        if cum >= target:
            break

    vah = max(va_prices)
    val = min(va_prices)

    return {
        "poc": poc_price,
        "vah": vah,
        "val": val,
        "high": d_high if d_high != float("-inf") else None,
        "low":  d_low if d_low  != float("inf")  else None,
    }

def print_mp(name: str, mp: dict):
    """
    Print + send MP snapshot.
    """
    send_telegram(
        f"{name} MP\nPOC: {mp['poc']:.1f}\nVAH: {mp['vah']:.1f}\nVAL: {mp['val']:.1f}"
    )
    print(f"\n=== {name} MP ===")
    print(f"POC: {mp['poc']:.1f}  VAH: {mp['vah']:.1f}  VAL: {mp['val']:.1f}")
    if mp["high"] is not None and mp["low"] is not None:
        print(f"Day High/Low: {mp['high']:.1f} / {mp['low']:.1f}")
    print("===================================")

# =====================================================
#            MARKET QUOTES (LTP + Volume)
# =====================================================

def fetch_quotes(instrument_keys):
    """
    /v2/market-quote/quotes
    Returns dict: key -> (ltp, volume)
    """
    if not instrument_keys:
        return {}
    joined = ",".join(instrument_keys)
    try:
        r = requests.get(
            f"{BASE_URL}/market-quote/quotes",
            headers=QUOTE_HEADERS,
            params={"instrument_key": joined},
            timeout=5,
        )
        js = r.json()
    except Exception as e:
        print("Quote exception:", e)
        return {}

    if js.get("status") != "success":
        print("Quote error:", js)
        return {}

    out = {}
    data = js.get("data", {})
    for key, q in data.items():
        ltp = q.get("last_price")
        if ltp is None:
            ohlc = q.get("ohlc") or {}
            ltp = ohlc.get("close")
        vol = q.get("volume", 0)
        if ltp is not None:
            out[key] = (float(ltp), float(vol or 0))
    return out

# =====================================================
#         OPTION CONTRACTS & ATM SELECTION
# =====================================================

def get_option_contracts(underlying_key: str, expiry_date: str):
    """
    /v2/option/contract?instrument_key=...&expiry_date=...
    """
    try:
        r = requests.get(
            f"{BASE_URL}/option/contract",
            headers=OPTION_HEADERS,
            params={"instrument_key": underlying_key, "expiry_date": expiry_date},
            timeout=8,
        )
        js = r.json()
        if js.get("status") != "success":
            print("Option contract error:", js)
            return []
        return js["data"]
    except Exception as e:
        print("Option contract exception:", e)
        return []

def round_nifty_strike(price: float) -> int:
    return int(round(price / 50.0) * 50)

def round_banknifty_strike(price: float) -> int:
    return int(round(price / 100.0) * 100)

def pick_atm_plus_minus(contracts, atm_strike: int, strikes_each_side: int):
    """
    Pick CE + PE for ATM ± N strikes.
    """
    results = []

    for opt_type in ("CE", "PE"):
        subset = [c for c in contracts if c.get("instrument_type") == opt_type]
        if not subset:
            continue

        strikes = sorted({int(c["strike_price"]) for c in subset})
        if not strikes:
            continue

        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - atm_strike))
        start = max(0, atm_idx - strikes_each_side)
        end = min(len(strikes) - 1, atm_idx + strikes_each_side)

        chosen = strikes[start : end + 1]
        for s in chosen:
            for c in subset:
                if int(c["strike_price"]) == s:
                    results.append((c["instrument_key"], c["trading_symbol"]))
                    break

    return
