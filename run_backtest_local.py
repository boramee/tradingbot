#!/usr/bin/env python3
"""
로컬 백테스트 — 네트워크 없이 합성 데이터로 전략 검증

실제 크립토 시장 특성을 반영한 합성 데이터:
  - 추세(상승/하락/횡보) 국면 전환
  - 급등/급락 이벤트
  - 변동성 클러스터링
  - 거래량 패턴 (추세 시 증가, 횡보 시 감소)
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from src.backtest.backtest_engine import BacktestEngine, STRATEGY_MAP


def generate_crypto_data(
    days: int = 365,
    base_price: float = 100_000_000,
    daily_vol: float = 0.025,
    seed: int = 42,
    name: str = "BTC",
) -> pd.DataFrame:
    """현실적 크립토 가격 데이터 생성

    - 4개 국면: 상승(+0.3%/일) → 횡보(0%) → 하락(-0.2%/일) → 반등(+0.5%/일)
    - 급등/급락 이벤트 (5% 확률)
    - 변동성 클러스터링 (GARCH-like)
    """
    rng = np.random.RandomState(seed)

    # 국면 정의
    regimes = []
    remaining = days
    while remaining > 0:
        regime_len = rng.randint(20, 60)
        regime_len = min(regime_len, remaining)
        regime_type = rng.choice(["bull", "bear", "sideways", "recovery"],
                                  p=[0.3, 0.25, 0.25, 0.2])
        regimes.append((regime_type, regime_len))
        remaining -= regime_len

    drift_map = {"bull": 0.003, "bear": -0.002, "sideways": 0.0, "recovery": 0.005}
    vol_map = {"bull": 0.02, "bear": 0.035, "sideways": 0.015, "recovery": 0.025}

    prices = [base_price]
    volumes = []
    vol_state = daily_vol

    for regime_type, regime_len in regimes:
        drift = drift_map[regime_type]
        target_vol = vol_map[regime_type]

        for _ in range(regime_len):
            # GARCH-like 변동성
            vol_state = 0.9 * vol_state + 0.1 * target_vol
            noise = rng.normal(0, vol_state)

            # 5% 확률 급등/급락 이벤트
            if rng.random() < 0.05:
                shock = rng.choice([-1, 1]) * rng.uniform(0.03, 0.08)
                noise += shock

            ret = drift + noise
            new_price = prices[-1] * (1 + ret)
            prices.append(max(new_price, prices[-1] * 0.5))  # 50% 이상 폭락 방지

            # 거래량: 변동성 클수록 높음
            base_vol = 1000 + abs(noise) * 50000
            if regime_type in ("bull", "recovery"):
                base_vol *= 1.3
            elif regime_type == "bear":
                base_vol *= 1.5  # 공포 매도
            volumes.append(base_vol * rng.uniform(0.5, 2.0))

    prices = np.array(prices[1:])
    n = len(prices)

    # OHLCV 생성
    highs = prices * (1 + np.abs(rng.normal(0, 0.008, n)))
    lows = prices * (1 - np.abs(rng.normal(0, 0.008, n)))
    opens = np.roll(prices, 1)
    opens[0] = base_price

    dates = pd.date_range("2025-03-24", periods=n, freq="D") - pd.Timedelta(days=n)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes[:n],
    }, index=dates)

    print("  [%s] %d일 | 시작: %s | 종료: %s | 변동: %.0f%%"
          % (name, n,
             "{:,.0f}".format(prices[0]),
             "{:,.0f}".format(prices[-1]),
             (prices[-1] - prices[0]) / prices[0] * 100))
    return df


def run_all_strategies(df, symbol, capital, stop_loss, take_profit, trailing):
    """모든 전략에 대해 백테스트 실행"""
    results = []
    for strat_name in STRATEGY_MAP:
        engine = BacktestEngine(
            strategy_name=strat_name,
            fee_rate=0.0005,
            slippage_pct=0.05,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            trailing_pct=trailing,
            atr_stop_mult=2.0,
            atr_trail_mult=1.5,
        )
        result = engine.run(df, initial_capital=capital, symbol=symbol)
        results.append(result)
        print(result.summary())

        if result.trades:
            print("\n  최근 5건:")
            for t in result.trades[-5:]:
                marker = "+" if t.pnl_pct > 0 else ""
                print("    %s→%s | %s→%s | %s%.2f%% | %d봉 | %s → %s"
                      % (t.entry_date, t.exit_date,
                         "{:,}".format(int(t.entry_price)),
                         "{:,}".format(int(t.exit_price)),
                         marker, t.pnl_pct, t.hold_bars,
                         t.reason_in[:25], t.reason_out[:20]))
        print()

    return results


def main():
    capital = 10_000_000
    stop_loss = 3.0
    take_profit = 4.0
    trailing = 1.5

    print("=" * 60)
    print("  크립토 백테스트 (합성 데이터, 현실적 시장 패턴)")
    print("  초기자금: %s원 | 수수료: 0.05%% | 슬리피지: 0.05%%" % "{:,}".format(capital))
    print("  손절: -%.1f%% | 익절: +%.1f%% | 트레일링: %.1f%%" % (stop_loss, take_profit, trailing))
    print("=" * 60)

    # ── 시나리오 1: BTC-like (낮은 변동성, 안정적 추세) ──
    print("\n📊 시나리오 1: BTC (안정적 추세, 낮은 변동성)")
    print("-" * 60)
    df_btc = generate_crypto_data(days=365, base_price=130_000_000, daily_vol=0.02,
                                   seed=42, name="BTC")
    results_btc = run_all_strategies(df_btc, "KRW-BTC", capital, stop_loss, take_profit, trailing)

    # ── 시나리오 2: ETH-like (중간 변동성) ──
    print("\n📊 시나리오 2: ETH (중간 변동성)")
    print("-" * 60)
    df_eth = generate_crypto_data(days=365, base_price=5_000_000, daily_vol=0.03,
                                   seed=123, name="ETH")
    results_eth = run_all_strategies(df_eth, "KRW-ETH", capital, stop_loss, take_profit, trailing)

    # ── 시나리오 3: XRP-like (높은 변동성, 급등/급락) ──
    print("\n📊 시나리오 3: XRP (높은 변동성, 급등/급락)")
    print("-" * 60)
    df_xrp = generate_crypto_data(days=365, base_price=3_000, daily_vol=0.04,
                                   seed=777, name="XRP")
    results_xrp = run_all_strategies(df_xrp, "KRW-XRP", capital, stop_loss, take_profit, trailing)

    # ── 시나리오 4: 하락장 (bear market) ──
    print("\n📊 시나리오 4: 하락장 스트레스 테스트")
    print("-" * 60)
    df_bear = generate_bear_market(days=180, base_price=100_000_000, seed=999)
    results_bear = run_all_strategies(df_bear, "BEAR-TEST", capital, stop_loss, take_profit, trailing)

    # ── 종합 비교 ──
    print("\n" + "=" * 60)
    print("  종합 비교표")
    print("=" * 60)
    print("  %-12s | %8s | %8s | %6s | %6s | %6s | %5s" % (
        "전략", "BTC", "ETH", "XRP", "하락장", "승률", "MDD"))
    print("  " + "-" * 65)

    for i, name in enumerate(STRATEGY_MAP):
        btc_r = results_btc[i].total_return_pct if i < len(results_btc) else 0
        eth_r = results_eth[i].total_return_pct if i < len(results_eth) else 0
        xrp_r = results_xrp[i].total_return_pct if i < len(results_xrp) else 0
        bear_r = results_bear[i].total_return_pct if i < len(results_bear) else 0

        all_trades = (
            (results_btc[i].trades if i < len(results_btc) else []) +
            (results_eth[i].trades if i < len(results_eth) else []) +
            (results_xrp[i].trades if i < len(results_xrp) else []) +
            (results_bear[i].trades if i < len(results_bear) else [])
        )
        total = len(all_trades)
        wins = sum(1 for t in all_trades if t.pnl_pct > 0)
        wr = wins / total * 100 if total > 0 else 0

        max_mdd = max(
            results_btc[i].max_drawdown_pct if i < len(results_btc) else 0,
            results_eth[i].max_drawdown_pct if i < len(results_eth) else 0,
            results_xrp[i].max_drawdown_pct if i < len(results_xrp) else 0,
            results_bear[i].max_drawdown_pct if i < len(results_bear) else 0,
        )

        print("  %-12s | %+7.1f%% | %+7.1f%% | %+5.1f%% | %+5.1f%% | %5.1f%% | %4.1f%%" % (
            name, btc_r, eth_r, xrp_r, bear_r, wr, max_mdd))

    # Buy & Hold 비교
    print("  " + "-" * 65)
    print("  %-12s | %+7.1f%% | %+7.1f%% | %+5.1f%% | %+5.1f%% |       |" % (
        "Buy&Hold",
        results_btc[0].buy_hold_pct,
        results_eth[0].buy_hold_pct,
        results_xrp[0].buy_hold_pct,
        results_bear[0].buy_hold_pct,
    ))
    print("=" * 60)


def generate_bear_market(days: int = 180, base_price: float = 100_000_000, seed: int = 999):
    """하락장 시나리오: 60% 하락 후 20% 반등"""
    rng = np.random.RandomState(seed)

    prices = [base_price]
    volumes = []

    # 급락 (120일 동안 60% 하락)
    for _ in range(int(days * 0.67)):
        ret = rng.normal(-0.005, 0.035)
        if rng.random() < 0.08:
            ret -= rng.uniform(0.03, 0.1)  # 패닉셀
        prices.append(prices[-1] * (1 + ret))
        volumes.append(2000 * rng.uniform(1, 3))

    # 반등 (60일 동안 20% 상승)
    for _ in range(days - int(days * 0.67)):
        ret = rng.normal(0.003, 0.025)
        prices.append(prices[-1] * (1 + ret))
        volumes.append(1500 * rng.uniform(0.5, 2))

    prices = np.array(prices[1:])
    n = len(prices)
    highs = prices * (1 + np.abs(rng.normal(0, 0.01, n)))
    lows = prices * (1 - np.abs(rng.normal(0, 0.01, n)))
    opens = np.roll(prices, 1)
    opens[0] = base_price

    dates = pd.date_range("2025-03-24", periods=n, freq="D") - pd.Timedelta(days=n)

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": prices, "volume": volumes[:n],
    }, index=dates)

    print("  [BEAR] %d일 | 시작: %s | 종료: %s | 변동: %.0f%%"
          % (n, "{:,.0f}".format(prices[0]), "{:,.0f}".format(prices[-1]),
             (prices[-1] - prices[0]) / prices[0] * 100))
    return df


if __name__ == "__main__":
    main()
