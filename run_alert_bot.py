#!/usr/bin/env python3
"""
한국 주식시장 공포/탐욕 알림봇 실행 스크립트

원칙: 시장이 탐욕적일 때 공포에 떨고, 시장이 공포에 떨 때 탐욕을 가져라

사용법:
  # 1회 분석
  python run_alert_bot.py

  # 반복 실행 (기본 60분 주기)
  python run_alert_bot.py --loop

  # 30분 주기로 반복
  python run_alert_bot.py --loop --interval 30

  # 특정 종목만 분석
  python run_alert_bot.py --codes 005930,000660,069500

  # KOSPI 지수 포함 분석
  python run_alert_bot.py --include-index
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="한국 주식시장 공포/탐욕 알림봇",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  %(prog)s                          # 기본 우량주 1회 분석
  %(prog)s --loop                   # 60분 주기 반복 분석
  %(prog)s --loop --interval 30     # 30분 주기 반복
  %(prog)s --codes 005930,000660    # 특정 종목만 분석
  %(prog)s --include-index          # KOSPI/KOSDAQ 지수 포함
        """,
    )
    parser.add_argument("--loop", action="store_true", help="반복 실행 모드")
    parser.add_argument("--interval", type=int, default=60, help="반복 주기 (분, 기본: 60)")
    parser.add_argument("--codes", type=str, default="", help="분석할 종목 코드 (쉼표 구분)")
    parser.add_argument("--include-index", action="store_true", help="KOSPI/KOSDAQ 지수 포함 분석")
    parser.add_argument("--no-telegram", action="store_true", help="텔레그램 알림 비활성화")
    parser.add_argument("--log-level", type=str, default="INFO", help="로그 레벨")

    args = parser.parse_args()

    from src.utils.logger import setup_logger
    setup_logger(args.log_level)

    from src.kr_stock.alert_bot import KRStockAlertBot
    from src.kr_stock.watchlist import Stock, WatchlistConfig
    from src.utils.telegram_bot import TelegramNotifier

    watchlist = WatchlistConfig()

    if args.codes:
        from src.kr_stock.watchlist import DEFAULT_WATCHLIST, ETF_WATCHLIST
        code_set = {c.strip() for c in args.codes.split(",")}
        all_known = {s.code: s for s in DEFAULT_WATCHLIST + ETF_WATCHLIST}
        custom_stocks = []
        custom_etfs = []
        for code in code_set:
            if code in all_known:
                s = all_known[code]
                if s.category == "ETF":
                    custom_etfs.append(s)
                else:
                    custom_stocks.append(s)
            else:
                custom_stocks.append(Stock(code, code, "사용자지정"))
        watchlist = WatchlistConfig(
            stocks=custom_stocks or [],
            etfs=custom_etfs or [],
        )

    telegram = None
    if not args.no_telegram:
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        telegram = TelegramNotifier(token, chat_id)

    bot = KRStockAlertBot(
        watchlist=watchlist,
        telegram=telegram,
        poll_interval_min=args.interval,
    )

    if args.include_index:
        _analyze_market_index(bot)

    if args.loop:
        bot.run_loop()
    else:
        bot.run_once()


def _analyze_market_index(bot: KRStockAlertBot):
    """KOSPI/KOSDAQ 지수를 별도 분석"""
    import logging
    logger = logging.getLogger(__name__)

    for market in ("KOSPI", "KOSDAQ"):
        df = bot.fetcher.fetch_market_index(market)
        if df is not None:
            result = bot.calculator.calculate(df)
            if result:
                logger.info(
                    "%s 지수 공포/탐욕: %.1f (%s) - 현재 %.2f",
                    market, result.score, result.sentiment.value, result.current_price,
                )


if __name__ == "__main__":
    main()
