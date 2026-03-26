#!/usr/bin/env python3
"""
백테스트 실행 스크립트 - 과거 데이터로 전략 수익률 검증

사용법:
  python3 run_backtest.py                                     # 삼성전자 1년 모든 전략
  python3 run_backtest.py --symbol 005930 --strategy rsi      # 삼성전자 RSI
  python3 run_backtest.py --symbol KRW-BTC --type coin        # BTC 코인
  python3 run_backtest.py --symbol KRW-XRP --type coin --strategy rsi
  python3 run_backtest.py --symbol 035720 --capital 5000000   # 카카오, 500만원

주식 종목코드: 005930(삼성전자), 000660(SK하이닉스), 035720(카카오)
코인: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.backtest.backtest_engine import BacktestEngine, STRATEGY_MAP


def fetch_stock_data(code: str, days: int = 365):
    """pykrx 우선, 실패 시 야후파이낸스(.KS)로 한국 주식 데이터 조회"""
    import pandas as pd
    from datetime import datetime, timedelta

    # pykrx로 시도 (한국 주식 과거 데이터)
    try:
        from pykrx import stock as pykrx_stock

        # 시스템 시간이 실제 거래 데이터 최신일보다 미래일 수 있어
        # end 날짜를 뒤로 이동하며 조회를 재시도한다.
        now = datetime.now()
        fallback_offsets = [0, 7, 30, 90, 180, 365, 730]
        for offset in fallback_offsets:
            end_dt = now - timedelta(days=offset)
            start_dt = end_dt - timedelta(days=days)
            end = end_dt.strftime("%Y%m%d")
            start = start_dt.strftime("%Y%m%d")
            df = pykrx_stock.get_market_ohlcv_by_date(start, end, code)
            if df is not None and not df.empty:
                df = df.iloc[:, :5]
                df.columns = ["open", "high", "low", "close", "volume"]
                name = pykrx_stock.get_market_ticker_name(code)
                print("데이터: %s %s (%d일, 기준일:%s)" % (code, name, len(df), end))
                return df
    except ImportError:
        print("pykrx 미설치 또는 로딩 실패 → yfinance 대체 조회 시도")

    # yfinance 폴백 (.KS)
    try:
        import yfinance as yf

        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days * 2)  # 휴장일 포함 여유 조회
        ticker = yf.Ticker("%s.KS" % code)
        df = ticker.history(start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"))
        if df is not None and not df.empty:
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            df = df[["open", "high", "low", "close", "volume"]].tail(days)
            print("데이터: %s (%s, yfinance, %d일)" % (code, "%s.KS" % code, len(df)))
            return df
    except Exception as e:
        print("yfinance 조회 실패: %s" % e)

    print("주식 데이터 조회 실패 (pykrx/yfinance 모두 실패)")
    return None


def fetch_us_data(symbol: str, days: int = 365):
    """야후 파이낸스에서 미국 주식 과거 데이터 조회"""
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import datetime, timedelta

        end = datetime.now()
        start = end - timedelta(days=days)
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

        if df is None or df.empty:
            print("데이터 없음: %s" % symbol)
            return None

        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]]
        name = ticker.info.get("shortName", symbol)
        print("데이터: %s %s (%d일)" % (symbol, name, len(df)))
        return df
    except ImportError:
        print("yfinance 패키지 필요: pip install yfinance")
        return None
    except Exception as e:
        print("미국 주식 데이터 조회 실패: %s" % e)
        return None


def fetch_coin_data(ticker: str, days: int = 365):
    """업비트에서 코인 과거 데이터 조회"""
    import pyupbit
    import pandas as pd

    # 업비트 API는 200개씩만 조회 가능 → 반복 조회
    all_df = []
    count = min(days, 200)
    to = None

    while days > 0:
        batch = min(days, 200)
        df = pyupbit.get_ohlcv(ticker, interval="day", count=batch, to=to)
        if df is None or df.empty:
            break
        df.columns = ["open", "high", "low", "close", "volume", "value"]
        df = df[["open", "high", "low", "close", "volume"]]
        all_df.append(df)
        to = str(df.index[0])
        days -= batch
        if len(df) < batch:
            break

    if not all_df:
        return None

    result = pd.concat(all_df).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    print("데이터: %s (%d일)" % (ticker, len(result)))
    return result


def main():
    parser = argparse.ArgumentParser(description="백테스트 - 과거 데이터로 전략 검증")
    parser.add_argument("--symbol", default="005930", help="종목코드 또는 코인티커")
    parser.add_argument("--type", choices=["stock", "coin", "us"], default="stock")
    parser.add_argument("--strategy", default="all",
                        choices=["rsi", "macd", "bollinger", "combined", "adaptive", "feargreed", "all"])
    parser.add_argument("--days", type=int, default=365, help="백테스트 기간 (일)")
    parser.add_argument("--capital", type=float, default=10_000_000, help="초기 자금")
    parser.add_argument("--fee", type=float, default=None, help="수수료 (주식:0.00015, 코인:0.0005)")
    parser.add_argument("--stop-loss", type=float, default=2.0)
    parser.add_argument("--take-profit", type=float, default=2.5)
    parser.add_argument("--trailing", type=float, default=1.0)

    args = parser.parse_args()

    # 자동 감지
    if args.symbol.startswith("KRW-"):
        args.type = "coin"
    elif args.symbol.isupper() and len(args.symbol) <= 5 and not args.symbol.isdigit():
        args.type = "us"

    # 수수료 기본값
    if args.fee is None:
        fees = {"coin": 0.0005, "stock": 0.00015, "us": 0.0025}
        args.fee = fees.get(args.type, 0.001)

    # 데이터 조회
    print("\n%s 데이터 조회 중... (%d일)" % (args.symbol, args.days))
    if args.type == "coin":
        df = fetch_coin_data(args.symbol, args.days)
    elif args.type == "us":
        df = fetch_us_data(args.symbol, args.days)
    else:
        df = fetch_stock_data(args.symbol, args.days)

    if df is None or len(df) < 30:
        print("데이터 부족 (최소 30일 필요)")
        return

    # 전략 실행
    strategies = list(STRATEGY_MAP.keys()) if args.strategy == "all" else [args.strategy]

    print("\n초기자금: %s원 | 수수료: %.3f%% | 기간: %d일"
          % ("{:,}".format(int(args.capital)), args.fee * 100, len(df)))

    best_result = None
    best_return = -999

    for strat_name in strategies:
        engine = BacktestEngine(
            strategy_name=strat_name,
            fee_rate=args.fee,
            stop_loss_pct=args.stop_loss,
            take_profit_pct=args.take_profit,
            trailing_pct=args.trailing,
        )
        result = engine.run(df, initial_capital=args.capital, symbol=args.symbol)
        print(result.summary())

        if result.total_return_pct > best_return:
            best_return = result.total_return_pct
            best_result = result

        # 최근 거래 표시
        if result.trades:
            print("\n  최근 5건:")
            for t in result.trades[-5:]:
                marker = "+" if t.pnl_pct > 0 else ""
                print("    %s → %s | %s → %s | %s%.2f%% | %s → %s"
                      % (t.entry_date, t.exit_date,
                         "{:,}".format(int(t.entry_price)),
                         "{:,}".format(int(t.exit_price)),
                         marker, t.pnl_pct,
                         t.reason_in[:20], t.reason_out[:20]))
        print()

    if best_result and len(strategies) > 1:
        print("=" * 55)
        print("  최고 전략: %s (%+.2f%%)" % (best_result.strategy, best_result.total_return_pct))
        print("=" * 55)


if __name__ == "__main__":
    main()
