"""백테스트 실행 스크립트"""

import sys

import pyupbit

from config.settings import AppConfig
from src.backtest.engine import BacktestEngine
from src.strategies import (
    RSIStrategy,
    MACDStrategy,
    BollingerStrategy,
    CombinedStrategy,
)
from src.utils.logger import setup_logger


def run_backtest(ticker: str = "KRW-BTC", interval: str = "day", count: int = 200):
    config = AppConfig()
    setup_logger(config.log_level)

    print(f"\n과거 데이터 조회 중... ({ticker}, {interval}, {count}개)")
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    if df is None or df.empty:
        print("데이터를 가져올 수 없습니다.")
        return

    df.columns = ["open", "high", "low", "close", "volume", "value"]
    print(f"데이터 기간: {df.index[0]} ~ {df.index[-1]}")

    engine = BacktestEngine(config)

    strategies = [
        RSIStrategy(config.indicator),
        MACDStrategy(config.indicator),
        BollingerStrategy(config.indicator),
        CombinedStrategy(config.indicator),
    ]

    for strategy in strategies:
        print(f"\n{'=' * 50}")
        print(f"  전략: {strategy.name}")
        print(f"{'=' * 50}")
        result = engine.run(df, strategy)
        print(result.summary())

        if result.trades:
            print("\n최근 5개 거래:")
            for trade in result.trades[-5:]:
                emoji = "+" if trade.profit_pct > 0 else ""
                print(
                    f"  {trade.entry_time:%Y-%m-%d} → {trade.exit_time:%Y-%m-%d} | "
                    f"{trade.entry_price:,.0f} → {trade.exit_price:,.0f} | "
                    f"{emoji}{trade.profit_pct:.2f}%"
                )


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "KRW-BTC"
    interval = sys.argv[2] if len(sys.argv) > 2 else "day"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    run_backtest(ticker, interval, count)
