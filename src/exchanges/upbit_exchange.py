"""업비트(Upbit) 거래소 클라이언트"""

import logging
from typing import Dict, List, Optional

import pyupbit

from config.settings import ExchangeKeys
from .base_exchange import BaseExchange, Ticker, OrderBook, OrderResult

logger = logging.getLogger(__name__)


class UpbitExchange(BaseExchange):
    """업비트 API 래퍼 - KRW 마켓"""

    SUPPORTED_TETHER = {"USDT"}

    def __init__(self, keys: ExchangeKeys):
        super().__init__(name="upbit", quote_currency="KRW", fee_rate=0.0005)
        self._upbit = None
        if keys.is_valid:
            self._upbit = pyupbit.Upbit(keys.access_key, keys.secret_key)

    @property
    def is_korean(self) -> bool:
        return True

    def _krw_ticker(self, symbol: str) -> str:
        return f"KRW-{symbol}"

    def _is_supported(self, symbol: str) -> bool:
        """업비트에서 지원하는 테더 토큰인지 확인"""
        from src.monitor.fx_rate import TETHER_PEG
        if symbol in TETHER_PEG:
            return symbol in self.SUPPORTED_TETHER
        return True

    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        if not self._is_supported(symbol):
            return None
        try:
            pair = self._krw_ticker(symbol)
            orderbook = pyupbit.get_orderbook(pair)
            if not orderbook:
                return None

            ob = orderbook[0] if isinstance(orderbook, list) else orderbook
            units = ob.get("orderbook_units", [])
            if not units:
                return None

            best_ask = float(units[0]["ask_price"])
            best_bid = float(units[0]["bid_price"])

            info = pyupbit.get_current_price(pair)
            last_price = float(info) if info else (best_ask + best_bid) / 2

            volume = 0.0
            try:
                ohlcv = pyupbit.get_ohlcv(pair, interval="day", count=1)
                if ohlcv is not None and not ohlcv.empty:
                    volume = float(ohlcv["volume"].iloc[-1])
            except Exception:
                pass

            return Ticker(
                exchange=self.name,
                symbol=symbol,
                quote=self.quote_currency,
                bid=best_bid,
                ask=best_ask,
                last=last_price,
                volume_24h=volume,
            )
        except Exception as e:
            logger.error("[Upbit] %s 시세 조회 실패: %s", symbol, e)
            return None

    def fetch_tickers(self, symbols: List[str]) -> Dict[str, Ticker]:
        result = {}
        for symbol in symbols:
            if not self._is_supported(symbol):
                continue
            ticker = self.fetch_ticker(symbol)
            if ticker:
                result[symbol] = ticker
        return result

    def fetch_orderbook(self, symbol: str) -> Optional[OrderBook]:
        try:
            pair = self._krw_ticker(symbol)
            data = pyupbit.get_orderbook(pair)
            if not data:
                return None

            ob = data[0] if isinstance(data, list) else data
            units = ob.get("orderbook_units", [])

            bids = [[float(u["bid_price"]), float(u["bid_size"])] for u in units]
            asks = [[float(u["ask_price"]), float(u["ask_size"])] for u in units]

            return OrderBook(exchange=self.name, symbol=symbol, bids=bids, asks=asks)
        except Exception as e:
            logger.error("[Upbit] %s 호가 조회 실패: %s", symbol, e)
            return None

    def get_balance(self, currency: str) -> float:
        if not self._upbit:
            return 0.0
        try:
            bal = self._upbit.get_balance(currency)
            return float(bal) if bal else 0.0
        except Exception as e:
            logger.error("[Upbit] %s 잔고 조회 실패: %s", currency, e)
            return 0.0

    def buy_market(self, symbol: str, amount_quote: float) -> Optional[OrderResult]:
        if not self._upbit:
            return OrderResult(self.name, symbol, "buy", 0, 0, success=False, error="미인증")
        pair = self._krw_ticker(symbol)
        try:
            result = self._upbit.buy_market_order(pair, amount_quote)
            if result and "error" not in result:
                return OrderResult(
                    exchange=self.name, symbol=symbol, side="buy",
                    price=0, amount=amount_quote,
                    fee=amount_quote * self.fee_rate,
                    order_id=str(result.get("uuid", "")),
                )
            error_msg = str(result.get("error", {}).get("message", "")) if result else "알 수 없음"
            return OrderResult(self.name, symbol, "buy", 0, 0, success=False, error=error_msg)
        except Exception as e:
            return OrderResult(self.name, symbol, "buy", 0, 0, success=False, error=str(e))

    def sell_market(self, symbol: str, amount_base: float) -> Optional[OrderResult]:
        if not self._upbit:
            return OrderResult(self.name, symbol, "sell", 0, 0, success=False, error="미인증")
        pair = self._krw_ticker(symbol)
        try:
            result = self._upbit.sell_market_order(pair, amount_base)
            if result and "error" not in result:
                return OrderResult(
                    exchange=self.name, symbol=symbol, side="sell",
                    price=0, amount=amount_base,
                    fee=0,
                    order_id=str(result.get("uuid", "")),
                )
            error_msg = str(result.get("error", {}).get("message", "")) if result else "알 수 없음"
            return OrderResult(self.name, symbol, "sell", 0, 0, success=False, error=error_msg)
        except Exception as e:
            return OrderResult(self.name, symbol, "sell", 0, 0, success=False, error=str(e))
