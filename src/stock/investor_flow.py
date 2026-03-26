"""외국인/기관 수급 데이터 (pykrx 기반)

스윙 전략 워치리스트 필터:
  - 외국인 3일 연속 순매도 종목 제외
  - 외국인+기관 동시 순매수 종목 가산점

사용:
  flow = InvestorFlow()
  info = flow.get_flow("005930", days=5)
  # → {"foreign_net": [100억, -50억, 200억, ...], "inst_net": [...],
  #    "foreign_consecutive_buy": 2, "both_buying": True}
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class InvestorFlow:
    """pykrx로 외국인/기관 순매수 데이터 조회"""

    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._cache_date: str = ""

    def get_flow(self, code: str, days: int = 5) -> Optional[Dict]:
        """종목의 최근 N일 외국인/기관 순매수 데이터 조회.

        Returns:
            {
                "foreign_net": [최근순 순매수액 리스트],
                "inst_net": [최근순 순매수액 리스트],
                "foreign_consecutive_buy": int (연속 순매수 일수),
                "foreign_consecutive_sell": int (연속 순매도 일수),
                "inst_consecutive_buy": int,
                "both_buying": bool (외국인+기관 동시 최근일 순매수),
            }
        """
        today = date.today().isoformat()
        if self._cache_date == today and code in self._cache:
            return self._cache[code]

        try:
            from pykrx import stock as pykrx_stock

            end = date.today()
            start = end - timedelta(days=days + 10)  # 주말/공휴일 대비 여유
            df = pykrx_stock.get_market_trading_value_by_date(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                code,
                on="순매수",
            )

            if df is None or df.empty:
                return None

            # 최근 N영업일만
            df = df.tail(days)

            # 컬럼: 기관합계, 기타법인, 개인, 외국인합계, 전체
            foreign_col = "외국인합계"
            inst_col = "기관합계"

            if foreign_col not in df.columns or inst_col not in df.columns:
                logger.warning("[수급] %s 컬럼 없음: %s", code, df.columns.tolist())
                return None

            foreign_net = [int(v) for v in df[foreign_col].values]
            inst_net = [int(v) for v in df[inst_col].values]

            result = {
                "foreign_net": foreign_net,
                "inst_net": inst_net,
                "foreign_consecutive_buy": self._consecutive(foreign_net, positive=True),
                "foreign_consecutive_sell": self._consecutive(foreign_net, positive=False),
                "inst_consecutive_buy": self._consecutive(inst_net, positive=True),
                "both_buying": len(foreign_net) > 0 and foreign_net[-1] > 0 and inst_net[-1] > 0,
            }

            self._cache[code] = result
            self._cache_date = today
            return result

        except Exception as e:
            logger.warning("[수급] %s 조회 실패: %s", code, e)
            return None

    @staticmethod
    def _consecutive(values: list, positive: bool = True) -> int:
        """뒤에서부터 연속 양수(또는 음수) 일수 카운트"""
        count = 0
        for v in reversed(values):
            if positive and v > 0:
                count += 1
            elif not positive and v < 0:
                count += 1
            else:
                break
        return count
