#!/usr/bin/env python3
"""삼성전자 자동매매 프로그램 실행

사용법:
    # 모의투자 (기본)
    python run_trader.py

    # 실전투자
    python run_trader.py --live

    # 전략 지정
    python run_trader.py --strategy rsi
    python run_trader.py --strategy macd
    python run_trader.py --strategy bollinger
    python run_trader.py --strategy ma_cross
    python run_trader.py --strategy combined

    # 종목 변경
    python run_trader.py --code 005935 --name 삼성전자우

    # 매매 주기 변경
    python run_trader.py --interval 30

    # 1회 분석만 실행
    python run_trader.py --once
"""

import argparse
import sys

from config.settings import AppConfig
from src.utils.logger import setup_logger
from src.trader.engine import TraderEngine


def parse_args():
    parser = argparse.ArgumentParser(
        description="삼성전자 자동매매 프로그램",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--live", action="store_true",
        help="실전투자 모드 (기본: 모의투자)",
    )
    parser.add_argument(
        "--strategy", type=str, default=None,
        choices=["rsi", "macd", "bollinger", "ma_cross", "combined"],
        help="매매 전략 (기본: .env의 STRATEGY 또는 combined)",
    )
    parser.add_argument(
        "--code", type=str, default=None,
        help="종목코드 (기본: 005930 삼성전자)",
    )
    parser.add_argument(
        "--name", type=str, default=None,
        help="종목명 (기본: 삼성전자)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="매매 체크 주기 (초, 기본: 60)",
    )
    parser.add_argument(
        "--stop-loss", type=float, default=None,
        help="손절 비율 (%%, 기본: 3.0)",
    )
    parser.add_argument(
        "--take-profit", type=float, default=None,
        help="익절 비율 (%%, 기본: 5.0)",
    )
    parser.add_argument(
        "--max-amount", type=int, default=None,
        help="1회 최대 매수 금액 (원, 기본: 1000000)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="1회만 분석 후 종료",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = AppConfig()

    if args.strategy:
        config.trading.strategy = args.strategy
    if args.code:
        config.trading.stock_code = args.code
    if args.name:
        config.trading.stock_name = args.name
    if args.interval:
        config.trading.poll_interval_sec = args.interval
    if args.stop_loss:
        config.trading.stop_loss_pct = args.stop_loss
    if args.take_profit:
        config.trading.take_profit_pct = args.take_profit
    if args.max_amount:
        config.trading.max_buy_amount = args.max_amount

    setup_logger(config.log_level)

    dry_run = not args.live
    engine = TraderEngine(config, dry_run=dry_run)

    if args.once:
        signal = engine.run_once()
        if signal:
            print(f"\n분석 결과: {signal.signal.value} (신뢰도: {signal.confidence:.1f})")
            print(f"사유: {signal.reason}")
            print(f"현재가: {signal.price:,.0f}원")
        else:
            print("\n분석 결과를 가져올 수 없습니다.")
        return

    try:
        engine.start()
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")
        sys.exit(0)


if __name__ == "__main__":
    main()
