#!/usr/bin/env python3
"""
기술적 분석 자동매매 봇 실행 스크립트.

사용법:
  python3 run_trader.py                                # 기본 (BTC MACD 1시간봉)
  python3 run_trader.py --mode scalp                   # 단타 프리셋
  python3 run_trader.py --mode swing                   # 스윙 프리셋
  python3 run_trader.py --ticker KRW-ETH --mode scalp  # ETH 단타
  python3 run_trader.py --ticker KRW-XRP --mode scalp  # XRP 단타 (변동성 큼)

프리셋:
  (기본):        MACD 1시간봉, 60초주기, 익절4%→트레일링1.5%, 손절ATRx2(폴백3%)
  scalp (단타):  5분봉, 15초주기, 익절1.8%→트레일링0.7%, 손절ATRx1.5(폴백1.5%)
  swing (스윙):  1시간봉, 60초주기, 익절4%→트레일링1.5%, 손절ATRx2(폴백3%)

커스텀 옵션으로 프리셋을 덮어쓸 수 있음:
  python3 run_trader.py --mode scalp --take-profit 2.0 --trailing 1.0
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

PRESETS = {
    "scalp": {
        "candle": "minute5",
        "interval": 15,
        "strategy": "adaptive",       # v3: rsi→adaptive (5분봉에서 RSI 30/70 거의 안 닿음)
        "invest_ratio": 0.05,
        "stop_loss": 1.5,             # v3: 2.0→1.5 (스캘프는 손절 빠르게)
        "take_profit": 1.8,           # v3: 2.5→1.8 (현실적 목표)
        "trailing": 0.7,              # v3: 1.0→0.7 (익절 더 타이트)
        "atr_mult": 1.5,
    },
    "swing": {
        "candle": "minute60",
        "interval": 60,
        "strategy": "combined",
        "invest_ratio": 0.1,
        "stop_loss": 3.0,
        "take_profit": 4.0,           # v3: 5.0→4.0 (더 자주 익절)
        "trailing": 1.5,              # v3: 2.0→1.5 (트레일링 타이트)
        "atr_mult": 2.0,
    },
}

# BTC 기본 프리셋 (백테스트 결과 기반: MACD 전략이 BTC에서 최고 성과)
# BTC 백테스트: +34% 수익, 60% 승률, PF 4.04, MDD 3.5%
BTC_DEFAULT = {
    "candle": "minute60",          # 1시간봉 (BTC는 노이즈 적은 긴 봉이 유리)
    "interval": 60,                # 60초마다 조회
    "strategy": "macd",            # MACD가 BTC에서 가장 안정적
    "invest_ratio": 0.1,           # 자금의 10%씩 투자
    "stop_loss": 3.0,              # ATR 폴백 손절 3%
    "take_profit": 4.0,            # 분할익절 기준 4%
    "trailing": 1.5,               # 트레일링 1.5%
    "atr_mult": 2.0,               # ATR 손절 배수
}


def main():
    parser = argparse.ArgumentParser(description="기술적 분석 자동매매 봇")
    parser.add_argument("--ticker", default=os.getenv("TICKER", "KRW-BTC"))
    parser.add_argument("--mode", choices=["scalp", "swing"], default=None,
                        help="프리셋 모드 (scalp=단타, swing=스윙)")
    parser.add_argument("--strategy", default=None,
                        choices=["rsi", "macd", "bollinger", "combined", "adaptive", "feargreed"])
    parser.add_argument("--interval", type=int, default=None, help="조회 주기 (초)")
    parser.add_argument("--candle", default=None,
                        help="캔들 간격 (minute1/minute3/minute5/minute15/minute60/day)")
    parser.add_argument("--invest-ratio", type=float, default=None)
    parser.add_argument("--max-invest", type=float, default=100000)
    parser.add_argument("--stop-loss", type=float, default=None, help="손절 폴백 (%%)")
    parser.add_argument("--take-profit", type=float, default=None, help="트레일링 활성화 기준 (%%)")
    parser.add_argument("--trailing", type=float, default=None, help="트레일링 폭 (%%)")
    parser.add_argument("--atr-mult", type=float, default=None, help="ATR 손절 배수")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()

    # 모드 미지정 시 BTC 최적화 기본값 사용 (백테스트 검증 완료)
    preset = PRESETS.get(args.mode) if args.mode else BTC_DEFAULT

    candle = args.candle or preset["candle"]
    interval = args.interval if args.interval is not None else preset["interval"]
    strategy = args.strategy or preset["strategy"]
    invest_ratio = args.invest_ratio if args.invest_ratio is not None else preset["invest_ratio"]
    stop_loss = args.stop_loss if args.stop_loss is not None else preset["stop_loss"]
    take_profit = args.take_profit if args.take_profit is not None else preset["take_profit"]
    trailing = args.trailing if args.trailing is not None else preset["trailing"]
    atr_mult = args.atr_mult if args.atr_mult is not None else preset["atr_mult"]

    setup_logger(args.log_level)

    engine = TraderEngine(
        access_key=os.getenv("UPBIT_ACCESS_KEY", ""),
        secret_key=os.getenv("UPBIT_SECRET_KEY", ""),
        ticker=args.ticker,
        strategy_name=strategy,
        interval=candle,
        invest_ratio=invest_ratio,
        max_invest_krw=args.max_invest,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        trailing_pct=trailing,
        atr_stop_multiplier=atr_mult,
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    engine.start(poll_sec=interval)


if __name__ == "__main__":
    main()
