"""고급 기술적 분석 지표"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class AdvancedIndicators:
    """피봇포인트, RSI 다이버전스, 시장 상태 분류"""

    @staticmethod
    def pivot_points(df: pd.DataFrame) -> Dict[str, float]:
        """전일 기준 피봇포인트 (지지/저항선)
        PP = (전일 고가 + 전일 저가 + 전일 종가) / 3
        S1/S2 = 지지선,  R1/R2 = 저항선
        """
        if len(df) < 2:
            return {}

        prev = df.iloc[-2]
        h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])

        pp = (h + l + c) / 3
        return {
            "r2": pp + (h - l),
            "r1": 2 * pp - l,
            "pp": pp,
            "s1": 2 * pp - h,
            "s2": pp - (h - l),
        }

    @staticmethod
    def detect_rsi_divergence(
        df: pd.DataFrame, lookback: int = 25
    ) -> Optional[str]:
        """RSI 다이버전스 감지

        상승 다이버전스: 가격은 저점 갱신 but RSI는 저점 높아짐 → 매수
        하락 다이버전스: 가격은 고점 갱신 but RSI는 고점 낮아짐 → 매도
        """
        if "rsi" not in df.columns or len(df) < lookback + 5:
            return None

        recent = df.iloc[-lookback:]
        prices = recent["close"].values
        rsis = recent["rsi"].values

        valid = ~np.isnan(rsis)
        if valid.sum() < lookback // 2:
            return None
        prices = prices[valid]
        rsis = rsis[valid]

        if len(prices) < 6:
            return None

        mid = len(prices) // 2
        price_left_min = np.min(prices[:mid])
        price_right_min = np.min(prices[mid:])
        rsi_at_price_left_min = rsis[np.argmin(prices[:mid])]
        rsi_at_price_right_min = rsis[mid + np.argmin(prices[mid:])]

        if price_right_min < price_left_min and rsi_at_price_right_min > rsi_at_price_left_min:
            return "bullish"

        price_left_max = np.max(prices[:mid])
        price_right_max = np.max(prices[mid:])
        rsi_at_price_left_max = rsis[np.argmax(prices[:mid])]
        rsi_at_price_right_max = rsis[mid + np.argmax(prices[mid:])]

        if price_right_max > price_left_max and rsi_at_price_right_max < rsi_at_price_left_max:
            return "bearish"

        return None

    @staticmethod
    def classify_market(df: pd.DataFrame) -> str:
        """시장 상태 분류: trending_up, trending_down, ranging, volatile

        ADX + 볼린저 밴드 폭 + DI 방향으로 판단.
        """
        if len(df) < 20:
            return "unknown"

        adx = df["adx"].iloc[-1] if "adx" in df.columns and pd.notna(df["adx"].iloc[-1]) else 0
        bb_upper = df["bb_upper"].iloc[-1] if "bb_upper" in df.columns and pd.notna(df["bb_upper"].iloc[-1]) else 0
        bb_lower = df["bb_lower"].iloc[-1] if "bb_lower" in df.columns and pd.notna(df["bb_lower"].iloc[-1]) else 0
        bb_mid = df["bb_mid"].iloc[-1] if "bb_mid" in df.columns and pd.notna(df["bb_mid"].iloc[-1]) else 0
        plus_di = df["plus_di"].iloc[-1] if "plus_di" in df.columns and pd.notna(df["plus_di"].iloc[-1]) else 0
        minus_di = df["minus_di"].iloc[-1] if "minus_di" in df.columns and pd.notna(df["minus_di"].iloc[-1]) else 0

        bb_width = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid > 0 else 0

        if bb_width > 12:
            return "volatile"

        if adx >= 25:
            if plus_di > minus_di:
                return "trending_up"
            return "trending_down"

        return "ranging"

    @staticmethod
    def near_support_resistance(
        price: float, pivots: Dict[str, float], tolerance_pct: float = 0.3
    ) -> Optional[str]:
        """현재 가격이 지지/저항선 근처인지 확인"""
        if not pivots:
            return None

        for level_name in ("s2", "s1"):
            level = pivots.get(level_name, 0)
            if level > 0 and abs(price - level) / level * 100 < tolerance_pct:
                return "near_support_%s" % level_name

        for level_name in ("r1", "r2"):
            level = pivots.get(level_name, 0)
            if level > 0 and abs(price - level) / level * 100 < tolerance_pct:
                return "near_resistance_%s" % level_name

        return None
