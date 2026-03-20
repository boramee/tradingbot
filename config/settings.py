"""삼성전자 자동매매 프로그램 설정"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = True) -> bool:
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes")


@dataclass
class KISConfig:
    """한국투자증권 API 설정"""
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    account_product_code: str = "01"
    is_paper: bool = True

    def __post_init__(self):
        self.app_key = _env("KIS_APP_KEY", self.app_key)
        self.app_secret = _env("KIS_APP_SECRET", self.app_secret)
        self.account_no = _env("KIS_ACCOUNT_NO", self.account_no)
        self.account_product_code = _env("KIS_ACCOUNT_PRODUCT_CODE", self.account_product_code)
        self.is_paper = _env_bool("KIS_IS_PAPER", self.is_paper)

    @property
    def is_valid(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)

    @property
    def base_url(self) -> str:
        if self.is_paper:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"


@dataclass
class TradingConfig:
    """매매 설정"""
    stock_code: str = "005930"
    stock_name: str = "삼성전자"
    strategy: str = "combined"
    max_buy_amount: int = 1_000_000
    max_hold_qty: int = 100
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 5.0
    poll_interval_sec: int = 60
    trading_start_time: str = "09:05"
    trading_end_time: str = "15:15"

    def __post_init__(self):
        self.stock_code = _env("STOCK_CODE", self.stock_code)
        self.stock_name = _env("STOCK_NAME", self.stock_name)
        self.strategy = _env("STRATEGY", self.strategy)
        self.max_buy_amount = _env_int("MAX_BUY_AMOUNT", self.max_buy_amount)
        self.max_hold_qty = _env_int("MAX_HOLD_QTY", self.max_hold_qty)
        self.stop_loss_pct = _env_float("STOP_LOSS_PCT", self.stop_loss_pct)
        self.take_profit_pct = _env_float("TAKE_PROFIT_PCT", self.take_profit_pct)
        self.poll_interval_sec = _env_int("POLL_INTERVAL_SEC", self.poll_interval_sec)
        self.trading_start_time = _env("TRADING_START_TIME", self.trading_start_time)
        self.trading_end_time = _env("TRADING_END_TIME", self.trading_end_time)


@dataclass
class TelegramConfig:
    """텔레그램 알림 설정"""
    token: str = ""
    chat_id: str = ""

    def __post_init__(self):
        self.token = _env("TELEGRAM_TOKEN", self.token)
        self.chat_id = _env("TELEGRAM_CHAT_ID", self.chat_id)

    @property
    def is_valid(self) -> bool:
        return bool(self.token and self.chat_id)


@dataclass
class AppConfig:
    """전체 애플리케이션 설정"""
    kis: KISConfig = field(default_factory=KISConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log_level: str = "INFO"

    def __post_init__(self):
        self.log_level = _env("LOG_LEVEL", "INFO")
