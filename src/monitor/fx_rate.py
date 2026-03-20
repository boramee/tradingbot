"""다중통화 환율 조회 모듈 (테더 토큰 페그 자산용)

환율 소스 우선순위:
  1. 두나무(업비트) API - 한국에서 가장 정확
  2. 업비트 BTC 가격 / 바이낸스 BTC 가격으로 USD/KRW 추정
  3. exchangerate.host 무료 API (글로벌)
  4. 하드코딩 폴백 값
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

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

_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 8


class FXRateProvider:
    """
    다중 통화 환율 제공.
    여러 소스를 순서대로 시도하여 최대한 실시간 환율을 확보.
    """

    CACHE_TTL = 60

    FALLBACK_RATES: Dict[str, float] = {
        "USD": 1350.0,
        "EUR": 1470.0,
        "CNH": 186.0,
        "XAU": 4_100_000.0,
    }

    def __init__(self):
        self._cache: Dict[str, float] = {}
        self._cache_time: float = 0

    def get_rate(self, currency: str) -> float:
        self._refresh_if_needed()
        return self._cache.get(currency, self.FALLBACK_RATES.get(currency, 0))

    def get_krw_per_usdt(self) -> float:
        return self.get_rate("USD")

    def get_all_rates(self) -> Dict[str, float]:
        self._refresh_if_needed()
        return dict(self._cache)

    def get_peg_rate(self, tether_symbol: str) -> float:
        peg = TETHER_PEG.get(tether_symbol)
        return self.get_rate(peg) if peg else 0.0

    def _refresh_if_needed(self):
        now = time.time()
        if self._cache and (now - self._cache_time) < self.CACHE_TTL:
            return

        got_usd = False
        got_usd = self._try_dunamu()
        if not got_usd:
            got_usd = self._try_upbit_binance_cross()
        if not got_usd:
            got_usd = self._try_exchangerate_host()

        if not got_usd and "USD" not in self._cache:
            self._cache["USD"] = self.FALLBACK_RATES["USD"]
            logger.warning("모든 환율 소스 실패 — 폴백 USD/KRW: %.0f", self._cache["USD"])

        self._derive_missing_rates()
        self._fetch_gold_price()
        self._cache_time = time.time()
        logger.debug("환율 갱신 완료: %s", {k: round(v, 2) for k, v in self._cache.items()})

    # ── 소스 1: 두나무(업비트) API ──

    def _try_dunamu(self) -> bool:
        codes = "FRX.KRWUSD,FRX.KRWEUR,FRX.KRWCNY,FRX.KRWJPY"
        try:
            resp = requests.get(
                "https://quotation-api-cdn.dunamu.com/v1/forex/recent",
                params={"codes": codes},
                timeout=_TIMEOUT,
                headers=_REQUEST_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                return False

            code_map = {
                "FRX.KRWUSD": "USD",
                "FRX.KRWEUR": "EUR",
                "FRX.KRWCNY": "CNH",
                "FRX.KRWJPY": "JPY",
            }
            found_usd = False
            for item in data:
                code = item.get("code", "")
                rate = float(item.get("basePrice", 0))
                currency = code_map.get(code)
                if currency and rate > 0:
                    self._cache[currency] = rate
                    self.FALLBACK_RATES[currency] = rate
                    if currency == "USD":
                        found_usd = True

            if found_usd:
                logger.debug("두나무 환율 조회 성공")
            return found_usd
        except Exception as e:
            logger.debug("두나무 환율 조회 실패: %s", e)
            return False

    # ── 소스 2: 업비트 KRW-BTC / 바이낸스 BTC-USDT 교차 계산 ──

    def _try_upbit_binance_cross(self) -> bool:
        try:
            import pyupbit
            krw_price = pyupbit.get_current_price("KRW-BTC")
            if not krw_price or krw_price <= 0:
                return False
        except Exception:
            return False

        try:
            import ccxt
            binance = ccxt.binance({"enableRateLimit": True, "timeout": 5000})
            ticker = binance.fetch_ticker("BTC/USDT")
            usdt_price = float(ticker.get("last", 0))
            if usdt_price <= 0:
                return False
        except Exception:
            return False

        usd_rate = krw_price / usdt_price
        if usd_rate > 500:
            self._cache["USD"] = usd_rate
            self.FALLBACK_RATES["USD"] = usd_rate
            logger.info("교차 환율 추정 USD/KRW: %.2f (BTC 기준)", usd_rate)
            return True
        return False

    # ── 소스 3: exchangerate.host 무료 글로벌 API ──

    def _try_exchangerate_host(self) -> bool:
        try:
            resp = requests.get(
                "https://api.exchangerate.host/latest",
                params={"base": "USD", "symbols": "KRW,EUR,CNY"},
                timeout=_TIMEOUT,
                headers=_REQUEST_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", {})

            krw = float(rates.get("KRW", 0))
            if krw > 0:
                self._cache["USD"] = krw
                self.FALLBACK_RATES["USD"] = krw

                eur_usd = float(rates.get("EUR", 0))
                if eur_usd > 0:
                    self._cache["EUR"] = krw / eur_usd
                cny_usd = float(rates.get("CNY", 0))
                if cny_usd > 0:
                    self._cache["CNH"] = krw / cny_usd

                logger.info("exchangerate.host 환율 조회 성공 USD/KRW: %.2f", krw)
                return True
        except Exception as e:
            logger.debug("exchangerate.host 조회 실패: %s", e)
        return False

    # ── 누락 환율 보완 ──

    def _derive_missing_rates(self):
        """USD 환율을 기반으로 다른 통화 환율 추정"""
        usd = self._cache.get("USD")
        if not usd:
            return

        if "EUR" not in self._cache:
            self._cache["EUR"] = usd * 1.09
        if "CNH" not in self._cache:
            self._cache["CNH"] = usd / 7.25

    # ── 금 시세 ──

    def _fetch_gold_price(self):
        usd_rate = self._cache.get("USD", self.FALLBACK_RATES["USD"])

        # XAUT/USD 시세로 추정 (Bitfinex에서 XAUT ≈ 금 1oz)
        try:
            import ccxt
            bf = ccxt.bitfinex2({"enableRateLimit": True, "timeout": 5000})
            ticker = bf.fetch_ticker("XAUT/USD")
            if ticker and ticker.get("last"):
                xau_usd = float(ticker["last"])
                if xau_usd > 100:
                    self._cache["XAU"] = xau_usd * usd_rate
                    return
        except Exception:
            pass

        if "XAU" not in self._cache:
            self._cache["XAU"] = self.FALLBACK_RATES["XAU"]

    # ── 변환 유틸 ──

    def convert_to_krw(self, amount: float, currency: str) -> float:
        rate = self.get_rate(currency)
        return amount * rate if rate > 0 else 0.0

    def convert_krw_to(self, krw_amount: float, currency: str) -> float:
        rate = self.get_rate(currency)
        return krw_amount / rate if rate > 0 else 0.0
