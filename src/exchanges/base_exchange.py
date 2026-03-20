"""거래소 추상 인터페이스 및 공통 데이터 모델"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import time


class ExchangeType(Enum):
    UPBIT = "upbit"
    BITHUMB = "bithumb"
    BINANCE = "binance"
    BYBIT = "bybit"


class QuoteCurrency(Enum):
    KRW = "KRW"
    USDT = "USDT"


@dataclass
class Ticker:
    exchange: str
    symbol: str          # e.g. "BTC"
    quote: str           # e.g. "KRW" or "USDT"
    bid: float           # 최고 매수호가 (내가 팔 수 있는 가격)
    ask: float           # 최저 매도호가 (내가 살 수 있는 가격)
    last: float          # 최종 체결가
    volume_24h: float    # 24시간 거래량 (base currency 기준)
    timestamp: float = field(default_factory=time.time)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        if self.bid <= 0:
            return 0.0
        return (self.ask - self.bid) / self.bid * 100

    @property
    def pair(self) -> str:
        return f"{self.symbol}/{self.quote}"


@dataclass
class OrderBook:
    exchange: str
    symbol: str
    bids: List[List[float]] = field(default_factory=list)  # [[price, amount], ...]
    asks: List[List[float]] = field(default_factory=list)

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def best_bid_volume(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def best_ask_volume(self) -> float:
        return self.asks[0][1] if self.asks else 0.0


@dataclass
class OrderResult:
    exchange: str
    symbol: str
    side: str           # "buy" or "sell"
    price: float
    amount: float
    fee: float = 0.0
    order_id: str = ""
    success: bool = True
    error: str = ""


class BaseExchange(ABC):
    """모든 거래소 클라이언트의 기본 인터페이스"""

    def __init__(self, name: str, quote_currency: str, fee_rate: float):
        self.name = name
        self.quote_currency = quote_currency
        self.fee_rate = fee_rate

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        """특정 코인의 현재 시세 조회"""
        ...

    @abstractmethod
    def fetch_tickers(self, symbols: List[str]) -> Dict[str, Ticker]:
        """여러 코인의 시세를 한번에 조회"""
        ...

    @abstractmethod
    def fetch_orderbook(self, symbol: str) -> Optional[OrderBook]:
        """호가창 조회"""
        ...

    @abstractmethod
    def get_balance(self, currency: str) -> float:
        """특정 화폐 잔고 조회"""
        ...

    @abstractmethod
    def buy_market(self, symbol: str, amount_quote: float) -> Optional[OrderResult]:
        """시장가 매수 (quote currency 금액 지정)"""
        ...

    @abstractmethod
    def sell_market(self, symbol: str, amount_base: float) -> Optional[OrderResult]:
        """시장가 매도 (base currency 수량 지정)"""
        ...

    @property
    @abstractmethod
    def is_korean(self) -> bool:
        """한국 거래소 여부"""
        ...

    def format_pair(self, symbol: str) -> str:
        """거래소별 심볼 포맷"""
        return f"{symbol}/{self.quote_currency}"
