"""복합 전략: RSI + MACD + 볼린저 + 이동평균선 가중 결합"""

from __future__ import annotations

import logging
from typing import List

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy

logger = logging.getLogger(__name__)

WEIGHTS = {"RSI": 0.30, "MACD": 0.35, "Bollinger": 0.35}


class CombinedStrategy(BaseStrategy):

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self._strategies: List[BaseStrategy] = [
            RSIStrategy(oversold, overbought),
            MACDStrategy(),
            BollingerStrategy(),
        ]

    @property
    def name(self) -> str:
        return "Combined"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        for strat in self._strategies:
            sig = strat.analyze(df)
            w = WEIGHTS.get(strat.name, 0.33)
            if sig.signal == Signal.BUY:
                buy_score += sig.confidence * w
                reasons.append("%s:매수" % strat.name)
            elif sig.signal == Signal.SELL:
                sell_score += sig.confidence * w
                reasons.append("%s:매도" % strat.name)

        # 이동평균 추세 보정
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        if ma_s is not None and ma_l is not None:
            if ma_s > ma_l:
                buy_score += 0.1
                reasons.append("MA:상승")
            elif ma_s < ma_l:
                sell_score += 0.1
                reasons.append("MA:하락")

        # 거래량 급등 보정
        vr = self._last(df, "vol_ratio")
        if vr is not None and vr > 1.5:
            boost = min(0.2, (vr - 1.5) * 0.1)
            buy_score *= (1 + boost)
            sell_score *= (1 + boost)

        price = float(df["close"].iloc[-1])
        tag = " | ".join(reasons) if reasons else "없음"

        if buy_score > sell_score and buy_score >= 0.3:
            return TradeSignal(Signal.BUY, min(1.0, buy_score), "복합매수: %s" % tag, price)
        if sell_score > buy_score and sell_score >= 0.3:
            return TradeSignal(Signal.SELL, min(1.0, sell_score), "복합매도: %s" % tag, price)
        return TradeSignal(Signal.HOLD, 0, "복합관망: %s" % tag, price)
