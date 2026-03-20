import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    access_key: str = ""
    secret_key: str = ""

    def __post_init__(self):
        self.access_key = os.getenv("UPBIT_ACCESS_KEY", "")
        self.secret_key = os.getenv("UPBIT_SECRET_KEY", "")

    @property
    def is_valid(self) -> bool:
        return bool(self.access_key and self.secret_key
                     and self.access_key != "your_access_key_here")


@dataclass
class TradingConfig:
    ticker: str = "KRW-BTC"
    investment_ratio: float = 0.1
    max_investment_krw: float = 100000
    strategy: str = "combined"
    interval: str = "minute60"
    candle_count: int = 200

    def __post_init__(self):
        self.ticker = os.getenv("TICKER", self.ticker)
        self.investment_ratio = float(os.getenv("INVESTMENT_RATIO", self.investment_ratio))
        self.max_investment_krw = float(os.getenv("MAX_INVESTMENT_KRW", self.max_investment_krw))
        self.strategy = os.getenv("STRATEGY", self.strategy)


@dataclass
class RiskConfig:
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 5.0
    max_daily_trades: int = 10
    max_position_ratio: float = 0.3

    def __post_init__(self):
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", self.stop_loss_pct))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", self.take_profit_pct))
        self.max_daily_trades = int(os.getenv("MAX_DAILY_TRADES", self.max_daily_trades))


@dataclass
class IndicatorConfig:
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    bb_period: int = 20
    bb_std: float = 2.0

    ma_short: int = 5
    ma_long: int = 20


@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    indicator: IndicatorConfig = field(default_factory=IndicatorConfig)
    log_level: str = "INFO"

    def __post_init__(self):
        self.log_level = os.getenv("LOG_LEVEL", self.log_level)
