"""멀티코인 상관관계 분석

BTC가 선행 지표 역할:
  BTC 5분 전 급등 시작 → ETH/XRP도 따라올 확률 높음
  BTC 급락 시작 → 알트코인 매수 차단

작동 방식:
  1. BTC의 직전 N분 변화율 추적
  2. BTC 상승 추세 → 알트코인 매수 신뢰도 상승
  3. BTC 급락 중 → 알트코인 매수 차단
  4. BTC-알트코인 디커플링 감지 → 주의
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Dict, Optional

import pyupbit

logger = logging.getLogger(__name__)


class CoinCorrelation:
    """BTC를 선행 지표로 사용하여 알트코인 매매 판단 보조"""

    CACHE_TTL = 30  # 30초 캐시
    _MAX_HISTORY = 180  # 최대 저장 개수 (10초 간격 × 30분 = 180)

    def __init__(self):
        self._btc_prices: deque = deque(maxlen=self._MAX_HISTORY)
        self._last_update: float = 0
        self._btc_trend: str = "neutral"  # up, down, neutral

    def update(self):
        """BTC 가격 추적 갱신"""
        now = time.time()
        if now - self._last_update < 10:
            return

        try:
            price = pyupbit.get_current_price("KRW-BTC")
            if price and price > 0:
                self._btc_prices.append((float(price), now))
                self._btc_trend = self._calc_trend()
                self._last_update = now
        except Exception:
            pass

    def _calc_trend(self) -> str:
        """최근 BTC 추세 판단"""
        if len(self._btc_prices) < 3:
            return "neutral"

        now = time.time()
        recent = self._btc_prices[-1][0]
        cutoff_5m = now - 300

        # 최근 5분 데이터만 필터 (deque는 시간순 → 뒤에서 검색)
        total = 0.0
        count = 0
        for price, ts in reversed(self._btc_prices):
            if ts < cutoff_5m:
                break
            total += price
            count += 1

        if count == 0:
            return "neutral"

        avg_5m = total / count
        change_5m = (recent - avg_5m) / avg_5m * 100

        if change_5m > 0.3:
            return "up"
        if change_5m < -0.3:
            return "down"
        return "neutral"

    def get_signal_modifier(self, coin: str) -> Dict:
        """알트코인 매매 시 BTC 상관관계 기반 보정값 반환"""
        self.update()

        result = {
            "btc_trend": self._btc_trend,
            "buy_allowed": True,
            "confidence_boost": 0.0,
            "reason": "",
        }

        if coin == "KRW-BTC":
            return result

        if self._btc_trend == "down":
            # BTC 하락 중 → 알트코인 매수 위험
            result["buy_allowed"] = False
            result["confidence_boost"] = -0.2
            result["reason"] = "BTC 하락 중 → 알트 매수 위험"

        elif self._btc_trend == "up":
            # BTC 상승 중 → 알트코인 동반 상승 기대
            result["confidence_boost"] = 0.15
            result["reason"] = "BTC 상승 중 → 알트 동반 상승 기대"

        return result

    @property
    def btc_change_5m(self) -> float:
        """BTC 5분 변화율"""
        if len(self._btc_prices) < 2:
            return 0.0
        recent = self._btc_prices[-1][0]
        cutoff = time.time() - 300
        # deque에서 5분 전 가격 찾기 (시간순 정렬)
        for price, ts in self._btc_prices:
            if ts >= cutoff:
                return (recent - price) / price * 100
        return 0.0
