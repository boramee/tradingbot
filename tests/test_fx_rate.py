"""환율 조회 테스트"""

import time

import pytest

from src.monitor.fx_rate import FXRateProvider, TETHER_PEG


class TestFXRateProvider:
    def test_returns_positive_rate(self):
        provider = FXRateProvider()
        rate = provider.get_rate("USD")
        assert rate > 0

    def test_multi_currency_cache(self):
        provider = FXRateProvider()
        provider._cache = {"USD": 1350.0, "EUR": 1470.0, "CNH": 186.0, "XAU": 4050000.0}
        provider._cache_time = time.time()

        assert provider.get_rate("USD") == 1350.0
        assert provider.get_rate("EUR") == 1470.0
        assert provider.get_rate("CNH") == 186.0
        assert provider.get_rate("XAU") == 4050000.0

    def test_peg_rate_usdt(self):
        provider = FXRateProvider()
        provider._cache = {"USD": 1350.0}
        provider._cache_time = time.time()
        assert provider.get_peg_rate("USDT") == 1350.0

    def test_peg_rate_eurt(self):
        provider = FXRateProvider()
        provider._cache = {"EUR": 1470.0}
        provider._cache_time = time.time()
        assert provider.get_peg_rate("EURT") == 1470.0

    def test_peg_rate_xaut(self):
        provider = FXRateProvider()
        provider._cache = {"XAU": 4050000.0}
        provider._cache_time = time.time()
        assert provider.get_peg_rate("XAUT") == 4050000.0

    def test_convert_to_krw(self):
        provider = FXRateProvider()
        provider._cache = {"USD": 1350.0}
        provider._cache_time = time.time()
        assert provider.convert_to_krw(100, "USD") == 135000.0

    def test_convert_krw_to(self):
        provider = FXRateProvider()
        provider._cache = {"USD": 1350.0}
        provider._cache_time = time.time()
        assert provider.convert_krw_to(135000, "USD") == 100.0

    def test_backward_compat_krw_per_usdt(self):
        provider = FXRateProvider()
        provider._cache = {"USD": 1350.0}
        provider._cache_time = time.time()
        assert provider.get_krw_per_usdt() == 1350.0

    def test_tether_peg_mapping(self):
        assert TETHER_PEG["USDT"] == "USD"
        assert TETHER_PEG["EURT"] == "EUR"
        assert TETHER_PEG["CNHT"] == "CNH"
        assert TETHER_PEG["XAUT"] == "XAU"
