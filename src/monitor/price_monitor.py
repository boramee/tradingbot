"""멀티 거래소 실시간 가격 모니터링"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.exchanges.base_exchange import BaseExchange, Ticker
from .fx_rate import FXRateProvider

logger = logging.getLogger(__name__)


@dataclass
class NormalizedPrice:
    """환율 보정된 통합 가격 (모두 USDT 기준으로 정규화)"""
    exchange: str
    symbol: str
    original_quote: str
    bid_usdt: float       # USDT 기준 매수호가
    ask_usdt: float       # USDT 기준 매도호가
    last_usdt: float      # USDT 기준 최종가
    bid_original: float   # 원래 통화 매수호가
    ask_original: float   # 원래 통화 매도호가
    volume_24h: float
    timestamp: float = field(default_factory=time.time)

    @property
    def mid_usdt(self) -> float:
        return (self.bid_usdt + self.ask_usdt) / 2


@dataclass
class PriceSnapshot:
    """특정 시점의 모든 거래소 가격 스냅샷"""
    symbol: str
    prices: Dict[str, NormalizedPrice] = field(default_factory=dict)
    fx_rate: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def exchange_count(self) -> int:
        return len(self.prices)


class PriceMonitor:
    """여러 거래소의 가격을 동시에 조회하고 USDT 기준으로 정규화"""

    USDT_SYMBOL = "USDT"

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
        """모든 거래소에서 모든 심볼의 가격을 동시에 조회"""
        fx_rate = self.fx_provider.get_krw_per_usdt()
        snapshots: Dict[str, PriceSnapshot] = {}

        coin_symbols = [s for s in self.symbols if s != self.USDT_SYMBOL]
        has_usdt = self.USDT_SYMBOL in self.symbols

        exchange_tickers: Dict[str, Dict[str, Ticker]] = {}

        if coin_symbols:
            with ThreadPoolExecutor(max_workers=len(self.exchanges)) as executor:
                futures = {
                    executor.submit(ex.fetch_tickers, coin_symbols): name
                    for name, ex in self.exchanges.items()
                }
                for future in as_completed(futures):
                    ex_name = futures[future]
                    try:
                        tickers = future.result()
                        exchange_tickers[ex_name] = tickers
                    except Exception as e:
                        logger.error("[%s] 가격 조회 실패: %s", ex_name, e)

        for symbol in coin_symbols:
            snapshot = PriceSnapshot(symbol=symbol, fx_rate=fx_rate)
            for ex_name, tickers in exchange_tickers.items():
                ticker = tickers.get(symbol)
                if not ticker or ticker.bid <= 0 or ticker.ask <= 0:
                    continue

                exchange = self.exchanges[ex_name]
                normalized = self._normalize(ticker, fx_rate, exchange.is_korean)
                snapshot.prices[ex_name] = normalized

            snapshots[symbol] = snapshot

        if has_usdt:
            usdt_snapshot = self._fetch_usdt_prices(fx_rate)
            snapshots[self.USDT_SYMBOL] = usdt_snapshot

        self._latest = snapshots
        return snapshots

    def _fetch_usdt_prices(self, fx_rate: float) -> PriceSnapshot:
        """
        USDT 가격 스냅샷 생성.

        - 한국 거래소: KRW-USDT 실제 거래 가격 조회
        - 해외 거래소: USDT/USDC 실제 거래 가격 조회
          (USDC ≈ $1 기준, USDT의 실시간 시장가를 반영)
        """
        snapshot = PriceSnapshot(symbol=self.USDT_SYMBOL, fx_rate=fx_rate)

        for ex_name, exchange in self.exchanges.items():
            ticker = exchange.fetch_ticker(self.USDT_SYMBOL)
            if not ticker or ticker.bid <= 0 or ticker.ask <= 0:
                continue

            if exchange.is_korean and ticker.quote == "KRW" and fx_rate > 0:
                snapshot.prices[ex_name] = NormalizedPrice(
                    exchange=ex_name,
                    symbol=self.USDT_SYMBOL,
                    original_quote="KRW",
                    bid_usdt=ticker.bid / fx_rate,
                    ask_usdt=ticker.ask / fx_rate,
                    last_usdt=ticker.last / fx_rate,
                    bid_original=ticker.bid,
                    ask_original=ticker.ask,
                    volume_24h=ticker.volume_24h,
                )
            else:
                # 해외: USDT/USDC 등 스테이블코인 기준 실제 가격
                snapshot.prices[ex_name] = NormalizedPrice(
                    exchange=ex_name,
                    symbol=self.USDT_SYMBOL,
                    original_quote=ticker.quote,
                    bid_usdt=ticker.bid,
                    ask_usdt=ticker.ask,
                    last_usdt=ticker.last,
                    bid_original=ticker.bid,
                    ask_original=ticker.ask,
                    volume_24h=ticker.volume_24h,
                )

        return snapshot

    def _normalize(
        self, ticker: Ticker, fx_rate: float, is_korean: bool
    ) -> NormalizedPrice:
        """KRW 가격은 USDT로 변환하여 정규화"""
        if is_korean and ticker.quote == "KRW" and fx_rate > 0:
            bid_usdt = ticker.bid / fx_rate
            ask_usdt = ticker.ask / fx_rate
            last_usdt = ticker.last / fx_rate
        else:
            bid_usdt = ticker.bid
            ask_usdt = ticker.ask
            last_usdt = ticker.last

        return NormalizedPrice(
            exchange=ticker.exchange,
            symbol=ticker.symbol,
            original_quote=ticker.quote,
            bid_usdt=bid_usdt,
            ask_usdt=ask_usdt,
            last_usdt=last_usdt,
            bid_original=ticker.bid,
            ask_original=ticker.ask,
            volume_24h=ticker.volume_24h,
        )

    @property
    def latest_snapshots(self) -> Dict[str, PriceSnapshot]:
        return self._latest
