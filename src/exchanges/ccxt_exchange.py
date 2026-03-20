"""ccxt 기반 범용 거래소 클라이언트 (Binance, Bybit, Bithumb 등)"""

import logging
from typing import Dict, List, Optional

import ccxt

from config.settings import ExchangeKeys
from .base_exchange import BaseExchange, Ticker, OrderBook, OrderResult

logger = logging.getLogger(__name__)

EXCHANGE_CONFIGS = {
    "binance": {
        "class": "binance",
        "quote": "USDT",
        "fee": 0.001,
        "korean": False,
    },
    "bybit": {
        "class": "bybit",
        "quote": "USDT",
        "fee": 0.001,
        "korean": False,
    },
    "bithumb": {
        "class": "bithumb",
        "quote": "KRW",
        "fee": 0.0025,
        "korean": True,
    },
    "bitfinex": {
        "class": "bitfinex2",
        "quote": "USD",
        "fee": 0.002,
        "korean": False,
    },
}

# 테더 토큰별 거래소별 조회 페어 (우선순위 순)
TETHER_PAIRS = {
    "USDT": {
        "binance":  ["USDT/USDC", "USDT/FDUSD"],
        "bybit":    ["USDT/USDC"],
        "bitfinex": ["USDT/USD"],
    },
    "EURT": {
        "binance":  ["EURT/USDT"],
        "bitfinex": ["EURT/USD", "EURT/USDT"],
    },
    "CNHT": {
        "bitfinex": ["CNHT/USDT", "CNHT/USD"],
    },
    "XAUT": {
        "binance":  ["XAUT/USDT"],
        "bybit":    ["XAUT/USDT"],
        "bitfinex": ["XAUT/USD", "XAUT/USDT"],
    },
}


class CcxtExchange(BaseExchange):
    """ccxt 라이브러리를 사용하는 범용 거래소 클라이언트"""

    def __init__(self, exchange_name: str, keys: ExchangeKeys):
        cfg = EXCHANGE_CONFIGS.get(exchange_name.lower())
        if not cfg:
            raise ValueError(f"지원하지 않는 거래소: {exchange_name}")

        super().__init__(
            name=exchange_name.lower(),
            quote_currency=cfg["quote"],
            fee_rate=cfg["fee"],
        )
        self._is_korean = cfg["korean"]

        exchange_cls = getattr(ccxt, cfg["class"])
        options = {
            "enableRateLimit": True,
            "timeout": 10000,
        }
        if keys.is_valid:
            options["apiKey"] = keys.access_key
            options["secret"] = keys.secret_key

        self._exchange: ccxt.Exchange = exchange_cls(options)

    @property
    def is_korean(self) -> bool:
        return self._is_korean

    def _make_pair(self, symbol: str) -> str:
        return f"{symbol}/{self.quote_currency}"

    def _is_tether_token(self, symbol: str) -> bool:
        return symbol in TETHER_PAIRS

    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        if self._is_tether_token(symbol):
            return self._fetch_tether_ticker(symbol)
        try:
            pair = self._make_pair(symbol)
            data = self._exchange.fetch_ticker(pair)
            return Ticker(
                exchange=self.name,
                symbol=symbol,
                quote=self.quote_currency,
                bid=float(data.get("bid") or 0),
                ask=float(data.get("ask") or 0),
                last=float(data.get("last") or 0),
                volume_24h=float(data.get("baseVolume") or 0),
                timestamp=float(data.get("timestamp") or 0) / 1000,
            )
        except ccxt.BadSymbol:
            logger.debug("[%s] %s 심볼 없음", self.name, symbol)
            return None
        except Exception as e:
            logger.error("[%s] %s 시세 조회 실패: %s", self.name, symbol, e)
            return None

    def _fetch_tether_ticker(self, symbol: str) -> Optional[Ticker]:
        """테더 토큰의 실제 거래 가격을 거래소별 전용 페어로 조회"""
        pairs = TETHER_PAIRS.get(symbol, {}).get(self.name, [])
        if not pairs:
            return None

        for pair in pairs:
            try:
                data = self._exchange.fetch_ticker(pair)
                bid = float(data.get("bid") or 0)
                ask = float(data.get("ask") or 0)
                last = float(data.get("last") or 0)
                if bid > 0 and ask > 0:
                    quote = pair.split("/")[1]
                    return Ticker(
                        exchange=self.name,
                        symbol=symbol,
                        quote=quote,
                        bid=bid,
                        ask=ask,
                        last=last,
                        volume_24h=float(data.get("baseVolume") or 0),
                        timestamp=float(data.get("timestamp") or 0) / 1000,
                    )
            except ccxt.BadSymbol:
                continue
            except Exception as e:
                logger.debug("[%s] %s 조회 실패 (%s): %s", self.name, symbol, pair, e)
                continue
        return None

    def fetch_tickers(self, symbols: List[str]) -> Dict[str, Ticker]:
        result = {}
        tether_symbols = [s for s in symbols if self._is_tether_token(s)]
        regular_symbols = [s for s in symbols if not self._is_tether_token(s)]

        if regular_symbols:
            try:
                pairs = [self._make_pair(s) for s in regular_symbols]
                all_tickers = self._exchange.fetch_tickers(pairs)
                for symbol in regular_symbols:
                    pair = self._make_pair(symbol)
                    if pair in all_tickers:
                        data = all_tickers[pair]
                        result[symbol] = Ticker(
                            exchange=self.name,
                            symbol=symbol,
                            quote=self.quote_currency,
                            bid=float(data.get("bid") or 0),
                            ask=float(data.get("ask") or 0),
                            last=float(data.get("last") or 0),
                            volume_24h=float(data.get("baseVolume") or 0),
                            timestamp=float(data.get("timestamp") or 0) / 1000,
                        )
            except Exception:
                for symbol in regular_symbols:
                    ticker = self.fetch_ticker(symbol)
                    if ticker:
                        result[symbol] = ticker

        for symbol in tether_symbols:
            ticker = self._fetch_tether_ticker(symbol)
            if ticker:
                result[symbol] = ticker

        return result

    def fetch_orderbook(self, symbol: str) -> Optional[OrderBook]:
        try:
            pair = self._make_pair(symbol)
            data = self._exchange.fetch_order_book(pair, limit=10)
            return OrderBook(
                exchange=self.name,
                symbol=symbol,
                bids=[[float(p), float(a)] for p, a in data.get("bids", [])],
                asks=[[float(p), float(a)] for p, a in data.get("asks", [])],
            )
        except Exception as e:
            logger.error("[%s] %s 호가 조회 실패: %s", self.name, symbol, e)
            return None

    def get_balance(self, currency: str) -> float:
        try:
            balance = self._exchange.fetch_balance()
            free = balance.get("free", {})
            return float(free.get(currency, 0))
        except Exception as e:
            logger.error("[%s] %s 잔고 조회 실패: %s", self.name, currency, e)
            return 0.0

    def buy_market(self, symbol: str, amount_quote: float) -> Optional[OrderResult]:
        pair = self._make_pair(symbol)
        try:
            ticker = self.fetch_ticker(symbol)
            if not ticker or ticker.ask <= 0:
                return OrderResult(self.name, symbol, "buy", 0, 0,
                                   success=False, error="가격 조회 실패")
            amount_base = amount_quote / ticker.ask
            result = self._exchange.create_market_buy_order(pair, amount_base)
            return OrderResult(
                exchange=self.name, symbol=symbol, side="buy",
                price=float(result.get("average") or result.get("price") or ticker.ask),
                amount=float(result.get("filled") or amount_base),
                fee=amount_quote * self.fee_rate,
                order_id=str(result.get("id", "")),
            )
        except Exception as e:
            logger.error("[%s] %s 매수 실패: %s", self.name, symbol, e)
            return OrderResult(self.name, symbol, "buy", 0, 0, success=False, error=str(e))

    def sell_market(self, symbol: str, amount_base: float) -> Optional[OrderResult]:
        pair = self._make_pair(symbol)
        try:
            result = self._exchange.create_market_sell_order(pair, amount_base)
            return OrderResult(
                exchange=self.name, symbol=symbol, side="sell",
                price=float(result.get("average") or result.get("price") or 0),
                amount=float(result.get("filled") or amount_base),
                fee=0,
                order_id=str(result.get("id", "")),
            )
        except Exception as e:
            logger.error("[%s] %s 매도 실패: %s", self.name, symbol, e)
            return OrderResult(self.name, symbol, "sell", 0, 0, success=False, error=str(e))
