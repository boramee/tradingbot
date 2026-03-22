"""백테스트 엔진 - 과거 데이터로 전략 수익률 검증

주식/코인 모두 지원. 실제 매매 엔진과 동일한 로직 적용:
  - RSI 반등 확인 후 진입
  - ATR 동적 손절 + 트레일링 익절 + 분할매도
  - 수수료 반영
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

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "rsi": RSIStrategy, "macd": MACDStrategy,
    "bollinger": BollingerStrategy, "combined": CombinedStrategy,
    "adaptive": AdaptiveStrategy,
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


@dataclass
class BacktestResult:
    symbol: str = ""
    strategy: str = ""
    period: str = ""
    initial_capital: float = 0
    final_capital: float = 0
    total_return_pct: float = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0
    avg_win_pct: float = 0
    avg_loss_pct: float = 0
    max_drawdown_pct: float = 0
    sharpe_ratio: float = 0
    profit_factor: float = 0
    trades: List[BacktestTrade] = field(default_factory=list)

    def summary(self) -> str:
        return "\n".join([
            "=" * 55,
            "  백테스트 결과: %s (%s)" % (self.symbol, self.strategy),
            "  기간: %s" % self.period,
            "=" * 55,
            "  초기자금:     %s원" % "{:,}".format(int(self.initial_capital)),
            "  최종자금:     %s원" % "{:,}".format(int(self.final_capital)),
            "  총 수익률:    %+.2f%%" % self.total_return_pct,
            "-" * 55,
            "  총 거래:      %d건" % self.total_trades,
            "  승리:         %d건" % self.winning_trades,
            "  패배:         %d건" % self.losing_trades,
            "  승률:         %.1f%%" % self.win_rate,
            "  평균 수익:    %+.2f%%" % self.avg_win_pct,
            "  평균 손실:    %.2f%%" % self.avg_loss_pct,
            "-" * 55,
            "  최대 낙폭:    %.2f%%" % self.max_drawdown_pct,
            "  샤프 비율:    %.2f" % self.sharpe_ratio,
            "  Profit Factor: %.2f" % self.profit_factor,
            "=" * 55,
        ])


class BacktestEngine:
    """과거 데이터 기반 전략 백테스트"""

    def __init__(
        self,
        strategy_name: str = "rsi",
        fee_rate: float = 0.0005,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 2.5,
        trailing_pct: float = 1.0,
        atr_stop_mult: float = 1.5,
        invest_ratio: float = 1.0,
    ):
        self.strategy = STRATEGY_MAP.get(strategy_name, RSIStrategy)()
        self.strategy_name = strategy_name
        self.ti = TechnicalIndicators()
        self.fee_rate = fee_rate
        self.round_trip_fee = fee_rate * 2 * 100
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_mult = atr_stop_mult
        self.invest_ratio = invest_ratio

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
        partial_sold = False
        coin_qty = 0.0
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = []
        entry_reason = ""
        entry_date = ""

        for i in range(warmup, len(df)):
            window = df.iloc[:i + 1]
            row = df.iloc[i]
            price = float(row["close"])
            date_str = str(df.index[i])[:10]
            atr = float(row["atr"]) if pd.notna(row.get("atr")) else 0

            if position:
                if price > highest:
                    highest = price

                net_pnl = (price - entry_price) / entry_price * 100 - self.round_trip_fee

                # 손절
                if entry_atr > 0:
                    stop_price = entry_price - entry_atr * self.atr_stop_mult
                    hit_stop = price <= stop_price
                else:
                    hit_stop = net_pnl <= -self.stop_loss_pct

                if hit_stop:
                    sell_val = coin_qty * price * (1 - self.fee_rate)
                    capital += sell_val
                    trades.append(BacktestTrade(
                        entry_date, date_str, entry_price, price, net_pnl,
                        entry_reason, "손절(%.1f%%)" % net_pnl))
                    position = False
                    continue

                # 분할매도
                partial_trigger = self.take_profit_pct * 0.6
                if not partial_sold and net_pnl >= partial_trigger:
                    half = coin_qty * 0.5
                    capital += half * price * (1 - self.fee_rate)
                    coin_qty -= half
                    partial_sold = True
                    continue

                # 트레일링
                if net_pnl >= self.take_profit_pct:
                    drop = (highest - price) / highest * 100
                    if drop >= self.trailing_pct:
                        sell_val = coin_qty * price * (1 - self.fee_rate)
                        capital += sell_val
                        trades.append(BacktestTrade(
                            entry_date, date_str, entry_price, price, net_pnl,
                            entry_reason, "트레일링(%.1f%%)" % net_pnl))
                        position = False
                        continue

                equity_curve.append(capital + coin_qty * price)

            else:
                equity_curve.append(capital)

                sig = self.strategy.analyze(window)
                if sig.signal == Signal.BUY and sig.is_actionable:
                    invest = capital * self.invest_ratio
                    fee = invest * self.fee_rate
                    coin_qty = (invest - fee) / price
                    capital -= invest
                    entry_price = price
                    entry_atr = atr
                    highest = price
                    partial_sold = False
                    position = True
                    entry_reason = sig.reason
                    entry_date = date_str

        # 마지막 포지션 청산
        if position:
            price = float(df["close"].iloc[-1])
            net_pnl = (price - entry_price) / entry_price * 100 - self.round_trip_fee
            sell_val = coin_qty * price * (1 - self.fee_rate)
            capital += sell_val
            trades.append(BacktestTrade(
                entry_date, str(df.index[-1])[:10], entry_price, price,
                net_pnl, entry_reason, "기간종료"))

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
        result = BacktestResult(
            symbol=symbol,
            strategy=self.strategy_name,
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return_pct=(final_capital - initial_capital) / initial_capital * 100,
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
                    result.sharpe_ratio = float(np.mean(returns) / std * np.sqrt(252))

        return result
