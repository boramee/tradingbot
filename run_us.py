#!/usr/bin/env python3
"""
미국 주식 자동매매 봇 (한국투자증권 해외주식 API)

사용법:
  python3 run_us.py                                # 기본 (AAPL,NVDA,TSLA)
  python3 run_us.py --symbols AAPL,MSFT,GOOGL      # 종목 지정
  python3 run_us.py --max-invest 1000               # 1회 최대 $1000
  python3 run_us.py --virtual                       # 모의투자

종목 예시:
  AAPL(애플) MSFT(MS) NVDA(엔비디아) TSLA(테슬라) AMZN(아마존)
  GOOGL(구글) META(메타) AMD NFLX(넷플릭스) AVGO(브로드컴)
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from src.utils.logger import setup_logger
from src.stock.us_engine import USStockEngine


def main():
    parser = argparse.ArgumentParser(description="미국 주식 자동매매 봇")
    parser.add_argument("--symbols", default="AAPL,NVDA,TSLA", help="종목 (쉼표 구분)")
    parser.add_argument("--strategy", default="bollinger",
                        choices=["rsi", "macd", "bollinger", "combined", "adaptive", "feargreed"])
    parser.add_argument("--interval", type=int, default=30, help="조회 주기 (초)")
    parser.add_argument("--invest-ratio", type=float, default=0.3)
    parser.add_argument("--max-invest", type=float, default=500, help="1회 최대 ($)")
    parser.add_argument("--stop-loss", type=float, default=2.0)
    parser.add_argument("--take-profit", type=float, default=3.0)
    parser.add_argument("--trailing", type=float, default=1.5)
    parser.add_argument("--virtual", action="store_true", help="모의투자")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    setup_logger(args.log_level)

    engine = USStockEngine(
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_no=os.getenv("KIS_ACCOUNT_NO", ""),
        account_prod=os.getenv("KIS_ACCOUNT_PROD", "01"),
        is_virtual=args.virtual,
        symbols=args.symbols,
        strategy_name=args.strategy,
        invest_ratio=args.invest_ratio,
        max_invest_usd=args.max_invest,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        trailing_pct=args.trailing,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    engine.start(poll_sec=args.interval)


if __name__ == "__main__":
    main()
