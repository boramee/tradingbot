"""복합 전략

RSI + MACD + 볼린저 밴드 + 이동평균 크로스를 가중 합산하여 판단.
거래량 필터도 적용하여 신뢰도를 높임.
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy
from .ma_cross import MACrossStrategy


class CombinedStrategy(BaseStrategy):

    def __init__(
        self,
        rsi_weight: float = 0.25,
        macd_weight: float = 0.30,
        bollinger_weight: float = 0.20,
        ma_weight: float = 0.25,
        min_volume_ratio: float = 0.8,
    ):
        self.weights = {
            "rsi": rsi_weight,
            "macd": macd_weight,
            "bollinger": bollinger_weight,
            "ma": ma_weight,
        }
        self.min_volume_ratio = min_volume_ratio
        self._strategies = {
            "rsi": RSIStrategy(),
            "macd": MACDStrategy(),
            "bollinger": BollingerStrategy(),
            "ma": MACrossStrategy(),
        }

    @property
    def name(self) -> str:
        return "Combined"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        signals = {}
        for key, strategy in self._strategies.items():
            signals[key] = strategy.analyze(df)

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        for key, sig in signals.items():
            w = self.weights[key]
            if sig.signal == Signal.BUY:
                buy_score += w * sig.confidence
                reasons.append(f"{key}:매수({sig.confidence:.1f})")
            elif sig.signal == Signal.SELL:
                sell_score += w * sig.confidence
                reasons.append(f"{key}:매도({sig.confidence:.1f})")

        vol_ratio = self._last(df, "vol_ratio")
        if vol_ratio and vol_ratio < self.min_volume_ratio:
            buy_score *= 0.7
            sell_score *= 0.7
            reasons.append(f"거래량부족({vol_ratio:.1f})")

        price = self._last(df, "close") or 0.0
        reason_str = ", ".join(reasons) if reasons else "신호 없음"

        if buy_score > sell_score and buy_score >= 0.3:
            return TradeSignal(Signal.BUY, min(buy_score, 1.0), f"복합매수: {reason_str}", price)

        if sell_score > buy_score and sell_score >= 0.3:
            return TradeSignal(Signal.SELL, min(sell_score, 1.0), f"복합매도: {reason_str}", price)

        return TradeSignal(Signal.HOLD, 0.0, f"복합관망: {reason_str}", price)
