"""백테스트 엔진 v2 - 실제 매매 엔진과 동일한 v3 로직 적용

변경점 (v1→v2):
  - 다단계 분할익절: 1차(60%) 30% + 2차(100%) 30% + 나머지 트레일링
  - ATR 기반 트레일링 스톱 (최고점에서 ATR×1.5 하락)
  - 신뢰도 기반 투자금 조절
  - 매도 신호에 의한 청산 추가
  - 슬리피지 시뮬레이션 (0.05%)
  - 재매수 쿨다운 (5봉)
  - Buy & Hold 대비 수익률 표시
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.indicators.technical import TechnicalIndicators
from src.strategies.base import BaseStrategy, Signal
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.combined import CombinedStrategy
from src.strategies.adaptive import AdaptiveStrategy
from src.strategies.fear_greed import FearGreedStrategy

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "rsi": RSIStrategy, "macd": MACDStrategy,
    "bollinger": BollingerStrategy, "combined": CombinedStrategy,
    "adaptive": AdaptiveStrategy,
    "feargreed": FearGreedStrategy,
}


@dataclass
class BacktestTrade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    reason_in: str
    reason_out: str
    hold_bars: int = 0


@dataclass
class BacktestResult:
    symbol: str = ""
    strategy: str = ""
    period: str = ""
    initial_capital: float = 0
    final_capital: float = 0
    total_return_pct: float = 0
    buy_hold_pct: float = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0
    avg_win_pct: float = 0
    avg_loss_pct: float = 0
    max_drawdown_pct: float = 0
    sharpe_ratio: float = 0
    profit_factor: float = 0
    avg_hold_bars: float = 0
    max_consecutive_loss: int = 0
    trades: List[BacktestTrade] = field(default_factory=list)

    def summary(self) -> str:
        alpha = self.total_return_pct - self.buy_hold_pct
        alpha_str = "%+.2f%%" % alpha
        verdict = "전략 우위" if alpha > 0 else "Buy&Hold 우위"
        return "\n".join([
            "=" * 60,
            "  백테스트 결과: %s (%s)" % (self.symbol, self.strategy),
            "  기간: %s" % self.period,
            "=" * 60,
            "  초기자금:     %s원" % "{:,}".format(int(self.initial_capital)),
            "  최종자금:     %s원" % "{:,}".format(int(self.final_capital)),
            "  총 수익률:    %+.2f%%" % self.total_return_pct,
            "  Buy&Hold:     %+.2f%%" % self.buy_hold_pct,
            "  알파:         %s (%s)" % (alpha_str, verdict),
            "-" * 60,
            "  총 거래:      %d건" % self.total_trades,
            "  승리:         %d건 | 패배: %d건" % (self.winning_trades, self.losing_trades),
            "  승률:         %.1f%%" % self.win_rate,
            "  평균 수익:    %+.2f%% | 평균 손실: %.2f%%" % (self.avg_win_pct, self.avg_loss_pct),
            "  평균 보유:    %.1f봉 | 최대 연패: %d건" % (self.avg_hold_bars, self.max_consecutive_loss),
            "-" * 60,
            "  최대 낙폭:    %.2f%%" % self.max_drawdown_pct,
            "  샤프 비율:    %.2f" % self.sharpe_ratio,
            "  Profit Factor: %.2f" % self.profit_factor,
            "=" * 60,
        ])


class BacktestEngine:
    """과거 데이터 기반 전략 백테스트 (v2 - 실전 엔진 동일 로직)"""

    def __init__(
        self,
        strategy_name: str = "rsi",
        fee_rate: float = 0.0005,
        slippage_pct: float = 0.05,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 2.5,
        trailing_pct: float = 1.0,
        atr_stop_mult: float = 1.5,
        atr_trail_mult: float = 1.5,
        invest_ratio: float = 1.0,
        rebuy_cooldown: int = 5,
    ):
        self.strategy = STRATEGY_MAP.get(strategy_name, RSIStrategy)()
        self.strategy_name = strategy_name
        self.ti = TechnicalIndicators()
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct / 100  # 0.05% → 0.0005
        self.round_trip_fee = fee_rate * 2 * 100
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_mult = atr_stop_mult
        self.atr_trail_mult = atr_trail_mult
        self.invest_ratio = invest_ratio
        self.rebuy_cooldown = rebuy_cooldown

    def _apply_slippage(self, price: float, is_buy: bool) -> float:
        """슬리피지 시뮬레이션: 매수 시 높게, 매도 시 낮게"""
        if is_buy:
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def run(
        self,
        df: pd.DataFrame,
        initial_capital: float = 10_000_000,
        symbol: str = "",
    ) -> BacktestResult:
        """백테스트 실행"""
        df = self.ti.add_all(df)
        warmup = 30

        if len(df) <= warmup:
            return BacktestResult(symbol=symbol, strategy=self.strategy_name)

        capital = initial_capital
        position = False
        entry_price = 0.0
        entry_atr = 0.0
        highest = 0.0
        partial_stage = 0
        coin_qty = 0.0
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = []
        entry_reason = ""
        entry_date = ""
        entry_bar = 0
        last_sell_bar = -999
        last_buy_price = 0.0

        for i in range(warmup, len(df)):
            window = df.iloc[:i + 1]
            row = df.iloc[i]
            price = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            date_str = str(df.index[i])[:10]
            atr = float(row["atr"]) if pd.notna(row.get("atr")) else 0

            if position:
                # 최고가 갱신 (고가 기준이 더 현실적)
                if high > highest:
                    highest = high

                net_pnl = (price - entry_price) / entry_price * 100 - self.round_trip_fee

                # ── 손절 (ATR 기반 우선, 폴백 고정%) ──
                if entry_atr > 0:
                    stop_price = entry_price - entry_atr * self.atr_stop_mult
                    hit_stop = low <= stop_price  # 저가 기준 체크
                    exit_price = self._apply_slippage(max(stop_price, low), False)
                else:
                    hit_stop = net_pnl <= -self.stop_loss_pct
                    exit_price = self._apply_slippage(price, False)

                if hit_stop:
                    actual_pnl = (exit_price - entry_price) / entry_price * 100 - self.round_trip_fee
                    sell_val = coin_qty * exit_price * (1 - self.fee_rate)
                    capital += sell_val
                    trades.append(BacktestTrade(
                        entry_date, date_str, entry_price, exit_price, actual_pnl,
                        entry_reason, "손절(%.1f%%)" % actual_pnl, i - entry_bar))
                    position = False
                    last_sell_bar = i
                    last_buy_price = entry_price
                    continue

                # ── 다단계 분할매도 ──
                tp = self.take_profit_pct

                # 1차: 60% 도달 시 30% 매도
                if partial_stage == 0 and net_pnl >= tp * 0.6:
                    sell_qty = coin_qty * 0.3
                    sell_price = self._apply_slippage(price, False)
                    capital += sell_qty * sell_price * (1 - self.fee_rate)
                    coin_qty -= sell_qty
                    partial_stage = 1
                    highest = price  # 리셋
                    continue

                # 2차: 100% 도달 시 30% 매도
                if partial_stage == 1 and net_pnl >= tp:
                    sell_qty = coin_qty * 0.3
                    sell_price = self._apply_slippage(price, False)
                    capital += sell_qty * sell_price * (1 - self.fee_rate)
                    coin_qty -= sell_qty
                    partial_stage = 2
                    highest = price  # 리셋
                    continue

                # ── ATR 기반 트레일링 (나머지 물량) ──
                min_pnl = tp * 0.5 if partial_stage >= 2 else tp
                if net_pnl >= min_pnl:
                    hit_trail = False
                    if entry_atr > 0:
                        trail_price = highest - entry_atr * self.atr_trail_mult
                        hit_trail = low <= trail_price
                        exit_price = self._apply_slippage(max(trail_price, low), False)
                    else:
                        drop = (highest - price) / highest * 100
                        hit_trail = drop >= self.trailing_pct
                        exit_price = self._apply_slippage(price, False)

                    if hit_trail:
                        actual_pnl = (exit_price - entry_price) / entry_price * 100 - self.round_trip_fee
                        sell_val = coin_qty * exit_price * (1 - self.fee_rate)
                        capital += sell_val
                        trades.append(BacktestTrade(
                            entry_date, date_str, entry_price, exit_price, actual_pnl,
                            entry_reason, "트레일링(%.1f%%)" % actual_pnl, i - entry_bar))
                        position = False
                        last_sell_bar = i
                        last_buy_price = entry_price
                        continue

                # ── 매도 신호에 의한 청산 ──
                sig = self.strategy.analyze(window)
                if sig.signal == Signal.SELL and sig.is_actionable and net_pnl > -self.stop_loss_pct * 0.5:
                    exit_price = self._apply_slippage(price, False)
                    actual_pnl = (exit_price - entry_price) / entry_price * 100 - self.round_trip_fee
                    sell_val = coin_qty * exit_price * (1 - self.fee_rate)
                    capital += sell_val
                    trades.append(BacktestTrade(
                        entry_date, date_str, entry_price, exit_price, actual_pnl,
                        entry_reason, "신호매도(%.1f%%)" % actual_pnl, i - entry_bar))
                    position = False
                    last_sell_bar = i
                    last_buy_price = entry_price
                    continue

                equity_curve.append(capital + coin_qty * price)

            else:
                equity_curve.append(capital)

                # 쿨다운 체크
                if i - last_sell_bar < self.rebuy_cooldown:
                    continue

                sig = self.strategy.analyze(window)
                if sig.signal == Signal.BUY and sig.is_actionable:
                    # 직전 매수가 근처 재매수 방지 (±1%)
                    if last_buy_price > 0:
                        diff = abs(price - last_buy_price) / last_buy_price * 100
                        if diff < 1.0:
                            continue

                    # 신뢰도 기반 투자금
                    conf_mult = 0.6 + sig.confidence * 1.2
                    invest = capital * self.invest_ratio * min(conf_mult, 1.8)

                    buy_price = self._apply_slippage(price, True)
                    fee = invest * self.fee_rate
                    coin_qty = (invest - fee) / buy_price
                    capital -= invest
                    entry_price = buy_price
                    entry_atr = atr
                    highest = high
                    partial_stage = 0
                    position = True
                    entry_reason = sig.reason
                    entry_date = date_str
                    entry_bar = i

        # 마지막 포지션 청산
        if position:
            price = float(df["close"].iloc[-1])
            exit_price = self._apply_slippage(price, False)
            net_pnl = (exit_price - entry_price) / entry_price * 100 - self.round_trip_fee
            sell_val = coin_qty * exit_price * (1 - self.fee_rate)
            capital += sell_val
            trades.append(BacktestTrade(
                entry_date, str(df.index[-1])[:10], entry_price, exit_price,
                net_pnl, entry_reason, "기간종료", len(df) - entry_bar))

        return self._compute_result(
            symbol, initial_capital, capital, trades, equity_curve, df)

    def _compute_result(
        self,
        symbol: str,
        initial_capital: float,
        final_capital: float,
        trades: List[BacktestTrade],
        equity_curve: List[float],
        df: pd.DataFrame,
    ) -> BacktestResult:
        # Buy & Hold 수익률
        buy_hold = 0.0
        if len(df) > 30:
            start_price = float(df["close"].iloc[30])
            end_price = float(df["close"].iloc[-1])
            if start_price > 0:
                buy_hold = (end_price - start_price) / start_price * 100

        result = BacktestResult(
            symbol=symbol,
            strategy=self.strategy_name,
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return_pct=(final_capital - initial_capital) / initial_capital * 100,
            buy_hold_pct=buy_hold,
            total_trades=len(trades),
            trades=trades,
        )

        if df is not None and len(df) > 1:
            result.period = "%s ~ %s" % (str(df.index[0])[:10], str(df.index[-1])[:10])

        if not trades:
            return result

        wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
        losses = [t.pnl_pct for t in trades if t.pnl_pct <= 0]

        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(trades) * 100
        result.avg_win_pct = np.mean(wins) if wins else 0
        result.avg_loss_pct = np.mean(losses) if losses else 0
        result.avg_hold_bars = np.mean([t.hold_bars for t in trades])

        # 최대 연패
        max_streak = 0
        streak = 0
        for t in trades:
            if t.pnl_pct <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        result.max_consecutive_loss = max_streak

        total_wins = sum(wins)
        total_losses = abs(sum(losses))
        result.profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        if equity_curve:
            eq = np.array(equity_curve)
            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / peak * 100
            result.max_drawdown_pct = float(np.max(dd)) if len(dd) > 0 else 0

            if len(eq) > 1:
                returns = np.diff(eq) / eq[:-1]
                std = np.std(returns)
                if std > 0:
                    # 크립토: 365일 기준 (24/7)
                    result.sharpe_ratio = float(np.mean(returns) / std * np.sqrt(365))

        return result
