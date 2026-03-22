"""한국 주식 데이터 수집 (pykrx 기반)"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class KRStockDataFetcher:
    """KRX에서 한국 주식 OHLCV 데이터를 가져온다."""

    def __init__(self, lookback_days: int = 400):
        self.lookback_days = lookback_days

    def _date_range(self) -> tuple[str, str]:
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def fetch_ohlcv(self, stock_code: str) -> Optional[pd.DataFrame]:
        from pykrx import stock as pykrx_stock

        start, end = self._date_range()
        try:
            df = pykrx_stock.get_market_ohlcv_by_date(start, end, stock_code)
            if df.empty:
                logger.warning("데이터 없음: %s", stock_code)
                return None

            df = df.rename(columns={
                "시가": "open",
                "고가": "high",
                "저가": "low",
                "종가": "close",
                "거래량": "volume",
            })
            df.index.name = "date"
            needed = ["open", "high", "low", "close", "volume"]
            df = df[[c for c in needed if c in df.columns]]
            df = df[df["volume"] > 0]
            return df

        except Exception as e:
            logger.error("OHLCV 조회 실패 (%s): %s", stock_code, e)
            return None

    def fetch_market_index(self, market: str = "KOSPI") -> Optional[pd.DataFrame]:
        from pykrx import stock as pykrx_stock

        start, end = self._date_range()
        try:
            df = pykrx_stock.get_index_ohlcv_by_date(start, end, "1001" if market == "KOSPI" else "2001")
            if df.empty:
                return None

            df = df.rename(columns={
                "시가": "open",
                "고가": "high",
                "저가": "low",
                "종가": "close",
                "거래량": "volume",
            })
            df.index.name = "date"
            needed = ["open", "high", "low", "close", "volume"]
            df = df[[c for c in needed if c in df.columns]]
            return df

        except Exception as e:
            logger.error("시장지수 조회 실패 (%s): %s", market, e)
            return None

    def fetch_multiple(self, stock_codes: list[str]) -> Dict[str, pd.DataFrame]:
        results: Dict[str, pd.DataFrame] = {}
        for code in stock_codes:
            df = self.fetch_ohlcv(code)
            if df is not None and not df.empty:
                results[code] = df
        return results
