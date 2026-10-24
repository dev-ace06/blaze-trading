from __future__ import annotations

import argparse
import json
import sys

import yfinance as yf

from algo.broker.kotak_neo import KotakNeoBroker
from algo.broker.paper import PaperBroker
from algo.config import load_config
from algo.engine import LiveEngine


def yfinance_quote(symbol: str) -> float:
    ticker = symbol if symbol.endswith(".NS") or symbol.startswith("^") else f"{symbol}.NS"
    frame = yf.download(ticker, period="1d", interval="1m", auto_adjust=False, progress=False, timeout=8)
    if frame.empty:
        raise RuntimeError(f"no live quote for {symbol}")
    value = frame["Close"].dropna().iloc[-1]
    return float(value.iloc[0] if hasattr(value, "iloc") else value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Sentinel ORB in paper or live mode")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--gift-nifty", type=float)
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and print schedule without network/order activity")
    parser.add_argument("--once", "--smoke", dest="once", action="store_true", help="Run one scheduler iteration and exit")
    args = parser.parse_args()
    config = load_config()

    if args.mode == "live":
        if not args.dry_run:
            confirmation = input("Live orders are enabled. Type CONFIRM to continue: ")
            if confirmation != "CONFIRM":
                print("Live startup cancelled.")
                return 2
        if args.dry_run:
            broker = PaperBroker(config.initial_equity, lambda _: 1.0, config.costs.slippage)
        else:
            broker = KotakNeoBroker(config.secrets)
    else:
        provider = (lambda _: 1.0) if args.dry_run else yfinance_quote
        broker = PaperBroker(config.initial_equity, provider, config.costs.slippage)

    engine = LiveEngine(config, broker, args.mode)
    status = engine.run(once=args.once, dry_run=args.dry_run)
    print(json.dumps(status.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
