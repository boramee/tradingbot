"""한국 주식시장 우량주 감시 목록"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    category: str = "개별주"


DEFAULT_WATCHLIST: List[Stock] = [
    Stock("005930", "삼성전자", "반도체"),
    Stock("000660", "SK하이닉스", "반도체"),
    Stock("373220", "LG에너지솔루션", "배터리"),
    Stock("006400", "삼성SDI", "배터리"),
    Stock("005380", "현대차", "자동차"),
    Stock("000270", "기아", "자동차"),
    Stock("035420", "NAVER", "플랫폼"),
    Stock("035720", "카카오", "플랫폼"),
    Stock("005490", "POSCO홀딩스", "소재"),
    Stock("051910", "LG화학", "화학"),
]

ETF_WATCHLIST: List[Stock] = [
    Stock("069500", "KODEX 200", "ETF"),
    Stock("229200", "KODEX 코스닥150", "ETF"),
    Stock("114800", "KODEX 인버스", "ETF"),
    Stock("252670", "KODEX 200선물인버스2X", "ETF"),
]

MARKET_INDICES = {
    "KOSPI": "코스피",
    "KOSDAQ": "코스닥",
}


@dataclass
class WatchlistConfig:
    stocks: List[Stock] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    etfs: List[Stock] = field(default_factory=lambda: list(ETF_WATCHLIST))

    @property
    def all_items(self) -> List[Stock]:
        return self.stocks + self.etfs

    def add_stock(self, code: str, name: str, category: str = "개별주"):
        self.stocks.append(Stock(code, name, category))

    def add_etf(self, code: str, name: str):
        self.etfs.append(Stock(code, name, "ETF"))
