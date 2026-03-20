"""다중통화 환율 조회 모듈 (테더 토큰 페그 자산용)"""

import logging
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# 테더 토큰별 페그 자산 매핑
TETHER_PEG = {
    "USDT": "USD",
    "EURT": "EUR",
    "CNHT": "CNH",
    "XAUT": "XAU",
}

TETHER_PEG_LABEL = {
    "USDT": "미국 달러 (USD)",
    "EURT": "유로 (EUR)",
    "CNHT": "역외 위안 (CNH)",
    "XAUT": "금 1oz (XAU)",
}


class FXRateProvider:
    """
    다중 통화 환율 제공.
    - USD/KRW, EUR/KRW, CNH/KRW: 두나무(업비트) API
    - XAU(금): 국제 금 시세 API
    """

    DUNAMU_URL = "https://quotation-api-cdn.dunamu.com/v1/forex/recent"
    CACHE_TTL = 60

    DUNAMU_CODES = {
        "USD": "FRX.KRWUSD",
        "EUR": "FRX.KRWEUR",
        "CNH": "FRX.KRWCNY",  # 두나무는 CNY(위안)만 제공, CNH와 근사
        "JPY": "FRX.KRWJPY",
    }

    FALLBACK_RATES = {
        "USD": 1350.0,
        "EUR": 1470.0,
        "CNH": 186.0,
        "XAU": 4_100_000.0,  # 금 1oz ≈ $3,000 × 1,350원
    }

    def __init__(self):
        self._cache: Dict[str, float] = {}
        self._cache_time: float = 0

    def get_rate(self, currency: str) -> float:
        """특정 통화의 KRW 환율 반환 (1 currency = ? KRW)"""
        self._refresh_if_needed()
        return self._cache.get(currency, self.FALLBACK_RATES.get(currency, 0))

    def get_krw_per_usdt(self) -> float:
        """하위 호환: 1 USDT = ? KRW"""
        return self.get_rate("USD")

    def get_all_rates(self) -> Dict[str, float]:
        """모든 환율 반환"""
        self._refresh_if_needed()
        return dict(self._cache)

    def get_peg_rate(self, tether_symbol: str) -> float:
        """테더 토큰의 페그 자산 KRW 환율"""
        peg = TETHER_PEG.get(tether_symbol)
        if not peg:
            return 0.0
        return self.get_rate(peg)

    def _refresh_if_needed(self):
        now = time.time()
        if self._cache and (now - self._cache_time) < self.CACHE_TTL:
            return

        self._fetch_dunamu_rates()
        self._fetch_gold_price()
        self._cache_time = now

    def _fetch_dunamu_rates(self):
        """두나무 API에서 주요 환율 일괄 조회"""
        codes = ",".join(self.DUNAMU_CODES.values())
        try:
            resp = requests.get(
                self.DUNAMU_URL,
                params={"codes": codes},
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data or not isinstance(data, list):
                return

            code_to_currency = {v: k for k, v in self.DUNAMU_CODES.items()}
            for item in data:
                code = item.get("code", "")
                rate = float(item.get("basePrice", 0))
                currency = code_to_currency.get(code)
                if currency and rate > 0:
                    self._cache[currency] = rate
                    self.FALLBACK_RATES[currency] = rate

            # CNH ≈ CNY (두나무는 CNY만 제공)
            if "CNH" not in self._cache and "CNY" in code_to_currency:
                cny_code = self.DUNAMU_CODES.get("CNH", "")
                for item in data:
                    if item.get("code") == cny_code:
                        rate = float(item.get("basePrice", 0))
                        if rate > 0:
                            self._cache["CNH"] = rate

            logger.debug("환율 갱신: %s", self._cache)
        except Exception as e:
            logger.warning("두나무 환율 조회 실패: %s", e)

    def _fetch_gold_price(self):
        """금 시세를 KRW로 조회 (1 troy oz 기준)"""
        # 방법 1: 금 달러 시세 × USD/KRW
        usd_rate = self._cache.get("USD", self.FALLBACK_RATES["USD"])
        try:
            resp = requests.get(
                "https://api.metalpriceapi.com/v1/latest?api_key=demo&base=XAU&currencies=USD",
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                xau_usd = data.get("rates", {}).get("USD", 0)
                if xau_usd > 0:
                    self._cache["XAU"] = xau_usd * usd_rate
                    return
        except Exception:
            pass

        # 방법 2: XAUT/USD 시세로 추정 (Bitfinex 등에서 XAUT ≈ 금시세)
        try:
            import ccxt
            bf = ccxt.bitfinex2({"enableRateLimit": True, "timeout": 5000})
            ticker = bf.fetch_ticker("XAUT/USD")
            if ticker and ticker.get("last"):
                xau_usd = float(ticker["last"])
                self._cache["XAU"] = xau_usd * usd_rate
                return
        except Exception:
            pass

        if "XAU" not in self._cache:
            self._cache["XAU"] = self.FALLBACK_RATES["XAU"]

    def convert_to_krw(self, amount: float, currency: str) -> float:
        rate = self.get_rate(currency)
        return amount * rate if rate > 0 else 0.0

    def convert_krw_to(self, krw_amount: float, currency: str) -> float:
        rate = self.get_rate(currency)
        return krw_amount / rate if rate > 0 else 0.0
