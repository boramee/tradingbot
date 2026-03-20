"""복합 전략 - 여러 전략의 신호를 종합하여 판단"""

import logging
from typing import List

import pandas as pd

from config.settings import IndicatorConfig
from .base_strategy import BaseStrategy, Signal, TradeSignal
from .rsi_strategy import RSIStrategy
from .macd_strategy import MACDStrategy
from .bollinger_strategy import BollingerStrategy

logger = logging.getLogger(__name__)


class CombinedStrategy(BaseStrategy):
    """
    복합 전략: RSI + MACD + 볼린저밴드 + 이동평균선의 신호를 가중 결합.
    다수결 원칙 + 신뢰도 가중평균으로 최종 판단.
    """

    def __init__(self, config: IndicatorConfig):
        super().__init__(config)
        self.strategies: List[BaseStrategy] = [
            RSIStrategy(config),
            MACDStrategy(config),
            BollingerStrategy(config),
        ]
        self.weights = {
            "RSI": 0.30,
            "MACD": 0.35,
            "Bollinger": 0.35,
        }

    @property
    def name(self) -> str:
        return "Combined"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        signals: List[TradeSignal] = []
        for strategy in self.strategies:
            sig = strategy.analyze(df)
            signals.append(sig)
            logger.debug("[%s] %s (신뢰도: %.2f) - %s",
                         strategy.name, sig.signal.value, sig.confidence, sig.reason)

        price = float(df["close"].iloc[-1])
        ma_signal = self._check_ma_trend(df)

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        for sig, strategy in zip(signals, self.strategies):
            weight = self.weights.get(strategy.name, 0.33)
            if sig.signal == Signal.BUY:
                buy_score += sig.confidence * weight
                reasons.append(f"{strategy.name}:매수")
            elif sig.signal == Signal.SELL:
                sell_score += sig.confidence * weight
                reasons.append(f"{strategy.name}:매도")

        if ma_signal == Signal.BUY:
            buy_score += 0.1
            reasons.append("MA:상승추세")
        elif ma_signal == Signal.SELL:
            sell_score += 0.1
            reasons.append("MA:하락추세")

        volume_boost = self._check_volume(df)
        buy_score *= (1 + volume_boost)
        sell_score *= (1 + volume_boost)

        reason_str = " | ".join(reasons) if reasons else "신호 없음"

        if buy_score > sell_score and buy_score >= 0.3:
            confidence = min(1.0, buy_score)
            return TradeSignal(Signal.BUY, confidence, f"복합매수: {reason_str}", price)

        if sell_score > buy_score and sell_score >= 0.3:
            confidence = min(1.0, sell_score)
            return TradeSignal(Signal.SELL, confidence, f"복합매도: {reason_str}", price)

        return TradeSignal(Signal.HOLD, 0.0, f"복합관망: {reason_str}", price)

    def _check_ma_trend(self, df: pd.DataFrame) -> Signal:
        ma_short = self._latest(df, "ma_short")
        ma_long = self._latest(df, "ma_long")
        if ma_short is not None and ma_long is not None:
            if ma_short > ma_long:
                return Signal.BUY
            if ma_short < ma_long:
                return Signal.SELL
        return Signal.HOLD

    def _check_volume(self, df: pd.DataFrame) -> float:
        """거래량이 평균보다 높으면 신뢰도 보정치 반환"""
        volume_ratio = self._latest(df, "volume_ratio")
        if volume_ratio is not None and volume_ratio > 1.5:
            return min(0.2, (volume_ratio - 1.5) * 0.1)
        return 0.0
