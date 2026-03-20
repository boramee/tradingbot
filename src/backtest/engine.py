"""백테스트 엔진 - 과거 데이터로 전략 성능 검증"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import numpy as np

from config.settings import AppConfig
from src.indicators.technical import TechnicalIndicators
from src.strategies.base_strategy import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    profit_pct: float = 0.0
    signal_reason: str = ""


@dataclass
class BacktestResult:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    avg_profit_pct: float = 0.0
    avg_loss_pct: float = 0.0
    sharpe_ratio: float = 0.0
    trades: List[Trade] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 50,
            "        백테스트 결과 리포트",
            "=" * 50,
            f"총 거래 횟수:     {self.total_trades}",
            f"승리:             {self.winning_trades}",
            f"패배:             {self.losing_trades}",
            f"승률:             {self.win_rate:.1f}%",
            f"총 수익률:        {self.total_profit_pct:.2f}%",
            f"평균 수익(승):    {self.avg_profit_pct:.2f}%",
            f"평균 손실(패):    {self.avg_loss_pct:.2f}%",
            f"최대 낙폭(MDD):   {self.max_drawdown_pct:.2f}%",
            f"샤프 비율:        {self.sharpe_ratio:.2f}",
            "=" * 50,
        ]
        return "\n".join(lines)


class BacktestEngine:
    """과거 데이터 기반 전략 백테스트"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.indicators = TechnicalIndicators(config.indicator)

    def run(
        self,
        df: pd.DataFrame,
        strategy: BaseStrategy,
        initial_capital: float = 1_000_000,
        fee_rate: float = 0.0005,
    ) -> BacktestResult:
        """
        백테스트 실행

        Args:
            df: OHLCV 데이터
            strategy: 테스트할 전략
            initial_capital: 초기 자본금 (KRW)
            fee_rate: 수수료율 (업비트 기준 0.05%)
        """
        df = self.indicators.add_all_indicators(df)
        warmup = 30
        if len(df) <= warmup:
            logger.error("백테스트용 데이터가 부족합니다 (최소 %d개 필요)", warmup)
            return BacktestResult()

        capital = initial_capital
        position = 0.0
        entry_price = 0.0
        trades: List[Trade] = []
        equity_curve: List[float] = []
        current_trade: Optional[Trade] = None

        for i in range(warmup, len(df)):
            window = df.iloc[:i + 1].copy()
            signal = strategy.analyze(window)

            current_price = float(df["close"].iloc[i])
            current_time = df.index[i]

            if position > 0:
                equity = capital + position * current_price
            else:
                equity = capital
            equity_curve.append(equity)

            if signal.signal == Signal.BUY and position == 0 and capital > 5000:
                invest = capital * self.config.trading.investment_ratio
                invest = min(invest, capital)
                fee = invest * fee_rate
                actual_invest = invest - fee
                position = actual_invest / current_price
                capital -= invest
                entry_price = current_price
                current_trade = Trade(
                    entry_time=current_time,
                    entry_price=current_price,
                    signal_reason=signal.reason,
                )

            elif signal.signal == Signal.SELL and position > 0:
                sell_value = position * current_price
                fee = sell_value * fee_rate
                capital += sell_value - fee
                profit_pct = (current_price - entry_price) / entry_price * 100

                if current_trade:
                    current_trade.exit_time = current_time
                    current_trade.exit_price = current_price
                    current_trade.profit_pct = profit_pct
                    trades.append(current_trade)
                    current_trade = None

                position = 0.0
                entry_price = 0.0

            elif position > 0:
                loss_pct = (entry_price - current_price) / entry_price * 100
                if loss_pct >= self.config.risk.stop_loss_pct:
                    sell_value = position * current_price
                    fee = sell_value * fee_rate
                    capital += sell_value - fee
                    profit_pct = (current_price - entry_price) / entry_price * 100

                    if current_trade:
                        current_trade.exit_time = current_time
                        current_trade.exit_price = current_price
                        current_trade.profit_pct = profit_pct
                        trades.append(current_trade)
                        current_trade = None
                    position = 0.0
                    entry_price = 0.0

                gain_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                if gain_pct >= self.config.risk.take_profit_pct:
                    sell_value = position * current_price
                    fee = sell_value * fee_rate
                    capital += sell_value - fee

                    if current_trade:
                        current_trade.exit_time = current_time
                        current_trade.exit_price = current_price
                        current_trade.profit_pct = gain_pct
                        trades.append(current_trade)
                        current_trade = None
                    position = 0.0
                    entry_price = 0.0

        if position > 0:
            final_price = float(df["close"].iloc[-1])
            sell_value = position * final_price
            capital += sell_value * (1 - fee_rate)
            if current_trade:
                current_trade.exit_time = df.index[-1]
                current_trade.exit_price = final_price
                current_trade.profit_pct = (final_price - entry_price) / entry_price * 100
                trades.append(current_trade)

        return self._compute_result(trades, equity_curve, initial_capital, capital)

    def _compute_result(
        self,
        trades: List[Trade],
        equity_curve: List[float],
        initial_capital: float,
        final_capital: float,
    ) -> BacktestResult:
        result = BacktestResult(trades=trades)
        result.total_trades = len(trades)

        if not trades:
            return result

        profits = [t.profit_pct for t in trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]

        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(trades) * 100
        result.total_profit_pct = (final_capital - initial_capital) / initial_capital * 100
        result.avg_profit_pct = np.mean(wins) if wins else 0.0
        result.avg_loss_pct = np.mean(losses) if losses else 0.0

        if equity_curve:
            equity = np.array(equity_curve)
            peak = np.maximum.accumulate(equity)
            drawdown = (peak - equity) / peak * 100
            result.max_drawdown_pct = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

            if len(equity) > 1:
                returns = np.diff(equity) / equity[:-1]
                if np.std(returns) > 0:
                    result.sharpe_ratio = float(np.mean(returns) / np.std(returns) * np.sqrt(252))

        return result
