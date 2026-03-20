#!/usr/bin/env python3
"""
기술적 분석 자동매매 봇 실행 스크립트.

사용법:
  python3 run_trader.py                          # 시뮬레이션 모드 (BTC, combined)
  python3 run_trader.py --ticker KRW-ETH         # ETH 대상
  python3 run_trader.py --strategy rsi            # RSI 전략
  python3 run_trader.py --interval 30             # 30초 주기
  python3 run_trader.py --stop-loss 5 --take-profit 8
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from src.utils.logger import setup_logger
from src.trader.engine import TraderEngine


def main():
    parser = argparse.ArgumentParser(description="기술적 분석 자동매매 봇")
    parser.add_argument("--ticker", default=os.getenv("TICKER", "KRW-BTC"), help="거래 대상 (기본: KRW-BTC)")
    parser.add_argument("--strategy", default=os.getenv("STRATEGY", "combined"),
                        choices=["rsi", "macd", "bollinger", "combined"], help="매매 전략")
    parser.add_argument("--interval", type=int, default=60, help="조회 주기 (초)")
    parser.add_argument("--candle", default="minute60", help="캔들 간격 (minute1/minute5/minute60/day)")
    parser.add_argument("--invest-ratio", type=float, default=0.1, help="KRW 잔고 대비 투자 비율")
    parser.add_argument("--max-invest", type=float, default=100000, help="1회 최대 투자 금액 (KRW)")
    parser.add_argument("--stop-loss", type=float, default=3.0, help="손절 기준 (%%)")
    parser.add_argument("--take-profit", type=float, default=5.0, help="익절 기준 (%%)")
    parser.add_argument("--log-level", default="INFO", help="로그 레벨")

    args = parser.parse_args()

    setup_logger(args.log_level)

    engine = TraderEngine(
        access_key=os.getenv("UPBIT_ACCESS_KEY", ""),
        secret_key=os.getenv("UPBIT_SECRET_KEY", ""),
        ticker=args.ticker,
        strategy_name=args.strategy,
        interval=args.candle,
        invest_ratio=args.invest_ratio,
        max_invest_krw=args.max_invest,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    engine.start(poll_sec=args.interval)


if __name__ == "__main__":
    main()
