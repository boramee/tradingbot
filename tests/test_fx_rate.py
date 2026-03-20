"""환율 조회 테스트"""

import pytest

from src.monitor.fx_rate import FXRateProvider


class TestFXRateProvider:
    def test_returns_positive_rate(self):
        provider = FXRateProvider()
        rate = provider.get_krw_per_usdt()
        assert rate > 0

    def test_fallback_rate(self):
        provider = FXRateProvider()
        provider._fallback_rate = 1400.0
        provider._cached_rate = None
        # 캐시가 없으면 API 호출 → 실패 시 폴백
        rate = provider.get_krw_per_usdt()
        assert rate > 0

    def test_caching(self):
        provider = FXRateProvider()
        provider._cached_rate = 1350.0
        provider._cache_time = __import__("time").time()
        rate = provider.get_krw_per_usdt()
        assert rate == 1350.0

    def test_convert_usdt_to_krw(self):
        provider = FXRateProvider()
        provider._cached_rate = 1350.0
        provider._cache_time = __import__("time").time()
        krw = provider.convert_usdt_to_krw(100)
        assert krw == 135000.0

    def test_convert_krw_to_usdt(self):
        provider = FXRateProvider()
        provider._cached_rate = 1350.0
        provider._cache_time = __import__("time").time()
        usdt = provider.convert_krw_to_usdt(135000)
        assert usdt == 100.0
