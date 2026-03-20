"""테더 토큰 전용 멀티 거래소 실시간 가격 모니터링"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.exchanges.base_exchange import BaseExchange, Ticker
from .fx_rate import FXRateProvider, TETHER_PEG

logger = logging.getLogger(__name__)


@dataclass
class NormalizedPrice:
    """환율 보정된 통합 가격"""
    exchange: str
    symbol: str
    original_quote: str       # 원래 거래 통화 (KRW, USD, USDT, USDC 등)
    price_in_peg: float       # 페그 자산 기준 가격 (USDT→USD, EURT→EUR 등)
    price_in_krw: float       # KRW 기준 가격
    bid_original: float
    ask_original: float
    last_original: float
    volume_24h: float
    peg_currency: str = ""    # 페그 자산 (USD, EUR, CNH, XAU)
    timestamp: float = field(default_factory=time.time)

    @property
    def mid_original(self) -> float:
        return (self.bid_original + self.ask_original) / 2


@dataclass
class PriceSnapshot:
    """특정 테더 토큰의 모든 거래소 가격 스냅샷"""
    symbol: str
    peg_currency: str = ""          # 페그 자산 (USD, EUR, CNH, XAU)
    peg_rate_krw: float = 0.0       # 페그 자산의 KRW 환율
    prices: Dict[str, NormalizedPrice] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def exchange_count(self) -> int:
        return len(self.prices)


class PriceMonitor:
    """테더 토큰의 거래소별 가격을 조회하고, 페그 자산 기준으로 정규화"""

    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        fx_provider: FXRateProvider,
        symbols: List[str],
    ):
        self.exchanges = exchanges
        self.fx_provider = fx_provider
        self.symbols = symbols
        self._latest: Dict[str, PriceSnapshot] = {}

    def fetch_all_prices(self) -> Dict[str, PriceSnapshot]:
        """모든 거래소에서 모든 테더 토큰의 가격을 동시에 조회"""
        rates = self.fx_provider.get_all_rates()
        snapshots: Dict[str, PriceSnapshot] = {}

        exchange_tickers: Dict[str, Dict[str, Ticker]] = {}
        with ThreadPoolExecutor(max_workers=len(self.exchanges)) as executor:
            futures = {
                executor.submit(ex.fetch_tickers, self.symbols): name
                for name, ex in self.exchanges.items()
            }
            for future in as_completed(futures):
                ex_name = futures[future]
                try:
                    tickers = future.result()
                    exchange_tickers[ex_name] = tickers
                except Exception as e:
                    logger.error("[%s] 가격 조회 실패: %s", ex_name, e)

        for symbol in self.symbols:
            peg = TETHER_PEG.get(symbol, "USD")
            peg_rate = rates.get(peg, 0)

            snapshot = PriceSnapshot(
                symbol=symbol,
                peg_currency=peg,
                peg_rate_krw=peg_rate,
            )

            for ex_name, tickers in exchange_tickers.items():
                ticker = tickers.get(symbol)
                if not ticker or ticker.bid <= 0 or ticker.ask <= 0:
                    continue

                normalized = self._normalize(ticker, peg, peg_rate, rates)
                if normalized:
                    snapshot.prices[ex_name] = normalized

            snapshots[symbol] = snapshot

        self._latest = snapshots
        return snapshots

    def _normalize(
        self,
        ticker: Ticker,
        peg_currency: str,
        peg_rate_krw: float,
        rates: Dict[str, float],
    ) -> Optional[NormalizedPrice]:
        """
        거래소 가격을 페그 자산 기준으로 정규화.

        예: USDT(페그=USD)
          - 업비트: 1,380 KRW → 1,380 / 1,350(USD환율) = $1.022
          - 바이낸스 USDT/USDC: 0.9998 → $0.9998 (USDC ≈ USD)
          - 비트파이넥스 USDT/USD: 1.0001 → $1.0001

        예: XAUT(페그=XAU)
          - 비트파이넥스 XAUT/USD: $3,050 → 3,050 / 3,000(금시세) = 1.017
        """
        quote = ticker.quote
        mid = (ticker.bid + ticker.ask) / 2

        if quote == "KRW":
            price_in_krw = mid
            price_in_peg = mid / peg_rate_krw if peg_rate_krw > 0 else 0
        elif quote == peg_currency:
            price_in_peg = mid
            price_in_krw = mid * peg_rate_krw
        elif quote in ("USDC", "USD"):
            usd_rate = rates.get("USD", 1350)
            price_in_krw = mid * usd_rate
            price_in_peg = price_in_krw / peg_rate_krw if peg_rate_krw > 0 else 0
        elif quote == "USDT":
            usd_rate = rates.get("USD", 1350)
            price_in_krw = mid * usd_rate
            price_in_peg = price_in_krw / peg_rate_krw if peg_rate_krw > 0 else 0
        else:
            quote_rate = rates.get(quote, 0)
            if quote_rate > 0:
                price_in_krw = mid * quote_rate
                price_in_peg = price_in_krw / peg_rate_krw if peg_rate_krw > 0 else 0
            else:
                return None

        return NormalizedPrice(
            exchange=ticker.exchange,
            symbol=ticker.symbol,
            original_quote=quote,
            price_in_peg=price_in_peg,
            price_in_krw=price_in_krw,
            bid_original=ticker.bid,
            ask_original=ticker.ask,
            last_original=ticker.last,
            volume_24h=ticker.volume_24h,
            peg_currency=peg_currency,
        )

    @property
    def latest_snapshots(self) -> Dict[str, PriceSnapshot]:
        return self._latest
