#!/usr/bin/env python3
"""
주식 자동매매 봇 실행 스크립트 (한국투자증권)

사용법:
  python3 run_stock.py                              # 삼성전자, combined
  python3 run_stock.py --code 035720                # 카카오
  python3 run_stock.py --code 005930 --strategy rsi # 삼성전자 RSI
  python3 run_stock.py --real                       # 실전 모드 (기본: 모의투자)

종목코드 예시:
  005930 삼성전자  |  000660 SK하이닉스  |  035720 카카오
  005380 현대차    |  035420 NAVER       |  051910 LG화학
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from src.utils.logger import setup_logger
from src.stock.stock_engine import StockEngine


def main():
    parser = argparse.ArgumentParser(description="주식 자동매매 봇 (한국투자증권)")
    parser.add_argument("--code", default="005930", help="종목코드 (기본: 005930 삼성전자)")
    parser.add_argument("--strategy", default="combined",
                        choices=["rsi", "macd", "bollinger", "combined"])
    parser.add_argument("--interval", type=int, default=10, help="조회 주기 (초)")
    parser.add_argument("--invest-ratio", type=float, default=0.1, help="투자 비율")
    parser.add_argument("--max-invest", type=int, default=500000, help="1회 최대 투자 (원)")
    parser.add_argument("--stop-loss", type=float, default=2.0, help="손절 (%%)")
    parser.add_argument("--take-profit", type=float, default=3.0, help="익절 기준 (%%)")
    parser.add_argument("--trailing", type=float, default=1.5, help="트레일링 폭 (%%)")
    parser.add_argument("--virtual", action="store_true", help="모의투자 모드 (기본: 실전)")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    setup_logger(args.log_level)

    engine = StockEngine(
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_no=os.getenv("KIS_ACCOUNT_NO", ""),
        account_prod=os.getenv("KIS_ACCOUNT_PROD", "01"),
        is_virtual=args.virtual,
        stock_code=args.code,
        strategy_name=args.strategy,
        invest_ratio=args.invest_ratio,
        max_invest_krw=args.max_invest,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        trailing_pct=args.trailing,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    engine.start(poll_sec=args.interval)


if __name__ == "__main__":
    main()
