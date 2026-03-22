#!/usr/bin/env python3
"""
거래소 간 재정거래 봇 (업비트 ↔ 바이낸스)

사용법:
  python3 run_arb.py                         # 시뮬레이션 (BTC,ETH,XRP)
  python3 run_arb.py --coins BTC,ETH,SOL     # 코인 지정
  python3 run_arb.py --min-profit 0.5        # 최소 순수익 0.5%
  python3 run_arb.py --max-trade 500000      # 1회 최대 50만원
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from src.utils.logger import setup_logger
from src.cross_arb.arb_engine import CrossArbEngine


def main():
    parser = argparse.ArgumentParser(description="거래소 간 재정거래 봇")
    parser.add_argument("--coins", default="BTC,ETH,XRP", help="대상 코인")
    parser.add_argument("--min-profit", type=float, default=0.3, help="최소 순수익률 (%%)")
    parser.add_argument("--max-trade", type=int, default=100000, help="1회 최대 거래 (KRW)")
    parser.add_argument("--slippage", type=float, default=0.1, help="슬리피지 (%%)")
    parser.add_argument("--interval", type=int, default=5, help="조회 주기 (초)")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    setup_logger(args.log_level)

    engine = CrossArbEngine(
        upbit_access=os.getenv("UPBIT_ACCESS_KEY", ""),
        upbit_secret=os.getenv("UPBIT_SECRET_KEY", ""),
        binance_access=os.getenv("BINANCE_ACCESS_KEY", ""),
        binance_secret=os.getenv("BINANCE_SECRET_KEY", ""),
        coins=args.coins,
        min_profit_pct=args.min_profit,
        max_trade_krw=args.max_trade,
        slippage_pct=args.slippage,
        poll_interval=args.interval,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    engine.start()


if __name__ == "__main__":
    main()
