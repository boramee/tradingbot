"""적응형 전략 - 시장 상태에 따라 자동 전략 전환

ADX + 볼린저 밴드폭 + ATR 변화율로 시장 상태를 분류하고,
그에 맞는 전략을 자동 선택.

  추세장 (ADX≥25, 정배열) → MACD (추세 추종)
  횡보장 (ADX<20, 밴드 좁음) → 볼린저 (평균 회귀)
  고변동장 (ATR 급증) → RSI (보수적, 반등 확인)
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy

logger = logging.getLogger(__name__)


class AdaptiveStrategy(BaseStrategy):

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self._rsi = RSIStrategy(oversold, overbought)
        self._macd = MACDStrategy()
        self._bollinger = BollingerStrategy()
        self._current_mode: str = "unknown"
        self._current_strategy: BaseStrategy = self._macd

    @property
    def name(self) -> str:
        return "Adaptive(%s)" % self._current_mode

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        new_mode = self._classify(df)
        price = float(df["close"].iloc[-1])

        if new_mode != self._current_mode:
            old = self._current_mode
            self._current_mode = new_mode
            self._current_strategy = self._pick(new_mode)
            strat_name = self._current_strategy.name if self._current_strategy else "매매중지"
            if old != "unknown":
                logger.info("[전략전환] %s → %s (전략: %s)", old, new_mode, strat_name)

        if self._current_strategy is None:
            return TradeSignal(Signal.HOLD, 0,
                               "[%s] 고변동장 매매 중지" % self._current_mode, price)

        sig = self._current_strategy.analyze(df)

        # 추세 하락장에서 매수 신호 차단
        if self._current_mode == "trend_down" and sig.signal == Signal.BUY:
            return TradeSignal(Signal.HOLD, sig.confidence * 0.3,
                               "[하락추세] 매수 차단: %s" % sig.reason, sig.price)

        return TradeSignal(
            sig.signal, sig.confidence,
            "[%s] %s" % (self._current_mode, sig.reason),
            sig.price,
        )

    def _classify(self, df: pd.DataFrame) -> str:
        """시장 상태 분류"""
        adx = self._last(df, "adx")
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        bb_upper = self._last(df, "bb_upper")
        bb_lower = self._last(df, "bb_lower")
        bb_mid = self._last(df, "bb_mid")
        atr = self._last(df, "atr")

        # ATR 변화율 (최근 5봉 평균 대비 현재)
        atr_surge = False
        if "atr" in df.columns and len(df) >= 10:
            recent_atr = df["atr"].iloc[-10:-1].mean()
            if pd.notna(recent_atr) and recent_atr > 0 and atr is not None:
                atr_surge = (atr / recent_atr) > 1.5

        # 볼린저 밴드폭
        bb_width = 0.0
        if bb_upper and bb_lower and bb_mid and bb_mid > 0:
            bb_width = (bb_upper - bb_lower) / bb_mid * 100

        # 고변동장: ATR 급증 또는 밴드폭 과대
        if atr_surge or bb_width > 8:
            return "volatile"

        # 추세장: ADX 높고 방향 명확
        if adx is not None and adx >= 25:
            if ma_s is not None and ma_l is not None:
                if ma_s > ma_l:
                    return "trend_up"
                else:
                    return "trend_down"
            return "trending"

        # 횡보장: ADX 낮고 밴드 좁음
        if adx is not None and adx < 20:
            return "ranging"

        return "neutral"

    def _pick(self, mode: str) -> Optional[BaseStrategy]:
        """시장 상태별 전략 선택. volatile이면 None(매매 중지)."""
        if mode in ("trend_up", "trend_down", "trending"):
            return self._macd
        elif mode == "ranging":
            return self._bollinger
        elif mode == "volatile":
            return None  # 고변동장에서는 매매 안 함
        return self._macd
