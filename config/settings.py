"""거래소 간 재정거래 봇 설정"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


@dataclass
class ExchangeKeys:
    access_key: str = ""
    secret_key: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.access_key and self.secret_key)


@dataclass
class ArbitrageConfig:
    target_symbols: List[str] = field(default_factory=lambda: ["USDT", "BTC", "ETH", "XRP", "SOL", "DOGE"])
    min_profit_pct: float = 0.5
    max_slippage_pct: float = 0.3
    max_trade_usdt: float = 1000.0
    poll_interval_sec: int = 2
    kimchi_buy_threshold: float = 1.0
    kimchi_sell_threshold: float = 3.0

    def __post_init__(self):
        symbols_str = _env("TARGET_SYMBOLS", "")
        if symbols_str:
            self.target_symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
        self.min_profit_pct = _env_float("MIN_PROFIT_PCT", self.min_profit_pct)
        self.max_slippage_pct = _env_float("MAX_SLIPPAGE_PCT", self.max_slippage_pct)
        self.max_trade_usdt = _env_float("MAX_TRADE_USDT", self.max_trade_usdt)
        self.poll_interval_sec = _env_int("POLL_INTERVAL_SEC", self.poll_interval_sec)
        self.kimchi_buy_threshold = _env_float("KIMCHI_BUY_THRESHOLD", self.kimchi_buy_threshold)
        self.kimchi_sell_threshold = _env_float("KIMCHI_SELL_THRESHOLD", self.kimchi_sell_threshold)


@dataclass
class AppConfig:
    upbit: ExchangeKeys = field(default_factory=lambda: ExchangeKeys())
    bithumb: ExchangeKeys = field(default_factory=lambda: ExchangeKeys())
    binance: ExchangeKeys = field(default_factory=lambda: ExchangeKeys())
    bybit: ExchangeKeys = field(default_factory=lambda: ExchangeKeys())
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    log_level: str = "INFO"

    def __post_init__(self):
        self.upbit = ExchangeKeys(_env("UPBIT_ACCESS_KEY"), _env("UPBIT_SECRET_KEY"))
        self.bithumb = ExchangeKeys(_env("BITHUMB_ACCESS_KEY"), _env("BITHUMB_SECRET_KEY"))
        self.binance = ExchangeKeys(_env("BINANCE_ACCESS_KEY"), _env("BINANCE_SECRET_KEY"))
        self.bybit = ExchangeKeys(_env("BYBIT_ACCESS_KEY"), _env("BYBIT_SECRET_KEY"))
        self.log_level = _env("LOG_LEVEL", "INFO")

    @property
    def active_exchanges(self) -> List[str]:
        """API 키가 설정된 거래소 목록"""
        result = []
        for name in ("upbit", "bithumb", "binance", "bybit"):
            keys: ExchangeKeys = getattr(self, name)
            if keys.is_valid:
                result.append(name)
        return result
