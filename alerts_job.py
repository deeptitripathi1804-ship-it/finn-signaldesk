"""Scheduled PUSH: run after market close (GitHub Action), message me when my tripwires fire.

  python alerts_job.py                      # prints the alert text
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python alerts_job.py   # also pushes to Telegram

Thresholds come from env (FINN_VOL_MULT, FINN_PRICE_PCT) - same rules as the app's watch-rule sliders.
Telegram was chosen because a bot token is free, takes 2 minutes, and lands as a phone notification.
"""
import os
import sys

import requests

from core import config, market


def main() -> int:
    vol = float(os.environ.get("FINN_VOL_MULT", config.DEFAULTS["vol_mult"]))
    px = float(os.environ.get("FINN_PRICE_PCT", config.DEFAULTS["price_pct"]))
    frames, errors, at, status = market.fetch_eod(config.SYMBOLS)
    radar = market.build_radar(frames, vol, px, config.DEFAULTS["lookback"])
    if radar.empty:
        print(f"No data ({status}); errors: {errors}")
        return 1
    hot = radar[radar["flagged"]]
    asof = radar["as_of"].max()
    if hot.empty:
        text = f"FINN · {asof:%d %b}: no tripwires (vol ≥ {vol}×, move ≥ ±{px}%)."
    else:
        lines = [f"FINN · {asof:%d %b} · {len(hot)} tripwire(s)"]
        for _, r in hot.iterrows():
            lines.append(f"• {r['symbol']} ₹{r['close']:,.1f} ({r['pct']:+.1f}%) - " + "; ".join(r["flags"]))
        text = "\n".join(lines)
    print(text)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        resp = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": chat, "text": text}, timeout=20)
        print("telegram:", resp.status_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
