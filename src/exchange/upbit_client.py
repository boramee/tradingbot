"""업비트 거래소 API 클라이언트"""

import logging
from typing import Optional

import pandas as pd
import pyupbit

from config.settings import ExchangeConfig, TradingConfig

logger = logging.getLogger(__name__)


class UpbitClient:
    """업비트 API를 래핑하여 주문, 잔고조회, 시세조회 기능 제공"""

    def __init__(self, config: ExchangeConfig, trading_config: TradingConfig):
        self.config = config
        self.trading_config = trading_config
        self._upbit: Optional[pyupbit.Upbit] = None

        if config.is_valid:
            self._upbit = pyupbit.Upbit(config.access_key, config.secret_key)
            logger.info("업비트 API 인증 완료")
        else:
            logger.warning("API 키가 설정되지 않았습니다. 시세 조회만 가능합니다.")

    @property
    def is_authenticated(self) -> bool:
        return self._upbit is not None

    def get_balance(self, ticker: str = "KRW") -> float:
        """특정 화폐의 잔고 조회"""
        if not self.is_authenticated:
            logger.error("인증되지 않은 상태에서 잔고 조회 시도")
            return 0.0
        try:
            balance = self._upbit.get_balance(ticker)
            return float(balance) if balance else 0.0
        except Exception as e:
            logger.error("잔고 조회 실패 [%s]: %s", ticker, e)
            return 0.0

    def get_current_price(self, ticker: Optional[str] = None) -> float:
        """현재가 조회"""
        ticker = ticker or self.trading_config.ticker
        try:
            price = pyupbit.get_current_price(ticker)
            return float(price) if price else 0.0
        except Exception as e:
            logger.error("현재가 조회 실패 [%s]: %s", ticker, e)
            return 0.0

    def get_ohlcv(
        self,
        ticker: Optional[str] = None,
        interval: Optional[str] = None,
        count: int = 200,
    ) -> Optional[pd.DataFrame]:
        """OHLCV(시가/고가/저가/종가/거래량) 데이터 조회"""
        ticker = ticker or self.trading_config.ticker
        interval = interval or self.trading_config.interval
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            if df is not None and not df.empty:
                df.columns = ["open", "high", "low", "close", "volume", "value"]
                return df
            logger.warning("OHLCV 데이터가 비어있습니다.")
            return None
        except Exception as e:
            logger.error("OHLCV 조회 실패: %s", e)
            return None

    def buy_market_order(self, ticker: Optional[str] = None, amount: float = 0) -> Optional[dict]:
        """시장가 매수 (KRW 금액 지정)"""
        if not self.is_authenticated:
            logger.error("인증되지 않은 상태에서 매수 시도")
            return None
        ticker = ticker or self.trading_config.ticker
        if amount < 5000:
            logger.warning("최소 주문 금액(5,000원) 미만: %.0f원", amount)
            return None
        try:
            result = self._upbit.buy_market_order(ticker, amount)
            logger.info("매수 주문 완료 [%s] %.0f원: %s", ticker, amount, result)
            return result
        except Exception as e:
            logger.error("매수 주문 실패 [%s]: %s", ticker, e)
            return None

    def sell_market_order(self, ticker: Optional[str] = None, volume: float = 0) -> Optional[dict]:
        """시장가 매도 (수량 지정)"""
        if not self.is_authenticated:
            logger.error("인증되지 않은 상태에서 매도 시도")
            return None
        ticker = ticker or self.trading_config.ticker
        if volume <= 0:
            logger.warning("매도 수량이 0 이하입니다.")
            return None
        try:
            result = self._upbit.sell_market_order(ticker, volume)
            logger.info("매도 주문 완료 [%s] %.8f개: %s", ticker, volume, result)
            return result
        except Exception as e:
            logger.error("매도 주문 실패 [%s]: %s", ticker, e)
            return None

    def get_avg_buy_price(self, ticker: Optional[str] = None) -> float:
        """평균 매수가 조회"""
        if not self.is_authenticated:
            return 0.0
        ticker = ticker or self.trading_config.ticker
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        try:
            avg_price = self._upbit.get_avg_buy_price(currency)
            return float(avg_price) if avg_price else 0.0
        except Exception as e:
            logger.error("평균 매수가 조회 실패: %s", e)
            return 0.0

    def get_holding_volume(self, ticker: Optional[str] = None) -> float:
        """보유 수량 조회"""
        if not self.is_authenticated:
            return 0.0
        ticker = ticker or self.trading_config.ticker
        currency = ticker.split("-")[1] if "-" in ticker else ticker
        return self.get_balance(currency)

    def get_investment_amount(self) -> float:
        """투자 가능 금액 계산 (KRW 기준)"""
        krw_balance = self.get_balance("KRW")
        amount = krw_balance * self.trading_config.investment_ratio
        return min(amount, self.trading_config.max_investment_krw)
