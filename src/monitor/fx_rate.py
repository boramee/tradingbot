"""환율(KRW/USD, KRW/USDT) 조회 모듈"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class FXRateProvider:
    """
    KRW ↔ USDT 환율 제공.
    두나무(업비트) API를 주 소스로, 실패 시 대체 소스 사용.
    """

    DUNAMU_URL = "https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD"
    CACHE_TTL = 60  # 캐시 유효시간(초)

    def __init__(self):
        self._cached_rate: Optional[float] = None
        self._cache_time: float = 0
        self._fallback_rate: float = 1350.0  # 기본 폴백 환율

    def get_krw_per_usdt(self) -> float:
        """1 USDT 당 KRW 환율 반환"""
        now = time.time()
        if self._cached_rate and (now - self._cache_time) < self.CACHE_TTL:
            return self._cached_rate

        rate = self._fetch_dunamu()
        if rate is None:
            rate = self._fetch_binance_p2p()
        if rate is None:
            logger.warning("환율 조회 실패 — 폴백 값 사용: %.0f", self._fallback_rate)
            rate = self._fallback_rate

        self._cached_rate = rate
        self._cache_time = now
        logger.debug("KRW/USDT 환율: %.2f", rate)
        return rate

    def _fetch_dunamu(self) -> Optional[float]:
        """두나무(업비트) API에서 USD/KRW 조회 → USDT ≈ USD로 근사"""
        try:
            resp = requests.get(self.DUNAMU_URL, timeout=5, headers={
                "User-Agent": "Mozilla/5.0"
            })
            resp.raise_for_status()
            data = resp.json()
            if data and isinstance(data, list):
                rate = float(data[0].get("basePrice", 0))
                if rate > 0:
                    self._fallback_rate = rate
                    return rate
        except Exception as e:
            logger.debug("두나무 환율 조회 실패: %s", e)
        return None

    def _fetch_binance_p2p(self) -> Optional[float]:
        """바이낸스 USDT/KRW 변환 가격으로 근사 (업비트 KRW-BTC / 바이낸스 BTC-USDT)"""
        try:
            import pyupbit
            krw_price = pyupbit.get_current_price("KRW-BTC")

            import ccxt
            binance = ccxt.binance({"enableRateLimit": True})
            ticker = binance.fetch_ticker("BTC/USDT")
            usdt_price = ticker["last"]

            if krw_price and usdt_price and usdt_price > 0:
                rate = krw_price / usdt_price
                if rate > 0:
                    self._fallback_rate = rate
                    return rate
        except Exception as e:
            logger.debug("바이낸스 P2P 환율 추정 실패: %s", e)
        return None

    def convert_usdt_to_krw(self, usdt_amount: float) -> float:
        """USDT → KRW 변환"""
        return usdt_amount * self.get_krw_per_usdt()

    def convert_krw_to_usdt(self, krw_amount: float) -> float:
        """KRW → USDT 변환"""
        rate = self.get_krw_per_usdt()
        if rate <= 0:
            return 0.0
        return krw_amount / rate
