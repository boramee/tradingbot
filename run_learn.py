#!/usr/bin/env python3
"""과거 거래 분석 + 학습 결과 확인

사용법:
  python3 run_learn.py              # 전체 거래 분석
  python3 run_learn.py --bot coin_trader    # 코인 봇만
  python3 run_learn.py --bot stock_trader   # 주식 봇만
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.intelligence.trade_learner import TradeLearner


def main():
    parser = argparse.ArgumentParser(description="과거 거래 학습")
    parser.add_argument("--bot", default="", help="봇 필터 (coin_trader/stock_trader/cross_arb/us_stock)")
    args = parser.parse_args()

    learner = TradeLearner()
    print(learner.get_recommendation(args.bot))


if __name__ == "__main__":
    main()
