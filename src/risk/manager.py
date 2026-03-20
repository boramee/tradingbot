"""재정거래 전용 리스크 관리"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config.settings import ArbitrageConfig
from src.arbitrage.detector import ArbitrageOpportunity

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    timestamp: float
    symbol: str
    buy_exchange: str
    sell_exchange: str
    profit_pct: float
    amount_usdt: float


class RiskManager:
    """
    재정거래 리스크 관리:
    1. 최소 순수익률 필터
    2. 슬리피지 보정
    3. 거래량 충분성 검증
    4. 일일 손실 한도
    5. 동시 거래 수 제한
    6. 쿨다운 (같은 페어 연속 거래 방지)
    """

    MAX_CONCURRENT = 3
    COOLDOWN_SEC = 30
    MAX_DAILY_LOSS_USDT = 50.0

    def __init__(self, config: ArbitrageConfig):
        self.config = config
        self._active_trades: int = 0
        self._trade_history: List[TradeRecord] = []
        self._last_trade_time: Dict[str, float] = {}  # "BTC:upbit→binance" → timestamp
        self._daily_pnl_usdt: float = 0.0
        self._pnl_date: str = ""

    def validate_opportunity(self, opp: ArbitrageOpportunity) -> tuple[bool, str]:
        """기회를 실행해도 되는지 리스크 관점에서 검증"""
        self._reset_daily_pnl_if_needed()

        if opp.net_profit_pct < self.config.min_profit_pct:
            return False, f"순수익률 부족 ({opp.net_profit_pct:.3f}% < {self.config.min_profit_pct}%)"

        slippage_adjusted = opp.net_profit_pct - self.config.max_slippage_pct
        if slippage_adjusted <= 0:
            return False, f"슬리피지 감안 시 수익 불가 ({slippage_adjusted:.3f}%)"

        if opp.buy_volume < 10 or opp.sell_volume < 10:
            return False, "거래량 부족 (24h 거래량 < 10)"

        if self._active_trades >= self.MAX_CONCURRENT:
            return False, f"동시 거래 한도 초과 ({self._active_trades}/{self.MAX_CONCURRENT})"

        trade_key = f"{opp.symbol}:{opp.buy_exchange}→{opp.sell_exchange}"
        last_time = self._last_trade_time.get(trade_key, 0)
        if time.time() - last_time < self.COOLDOWN_SEC:
            remaining = self.COOLDOWN_SEC - (time.time() - last_time)
            return False, f"쿨다운 중 ({remaining:.0f}초 남음)"

        if self._daily_pnl_usdt < -self.MAX_DAILY_LOSS_USDT:
            return False, f"일일 손실 한도 초과 ({self._daily_pnl_usdt:.2f} USDT)"

        return True, "검증 통과"

    def calculate_trade_amount(self, opp: ArbitrageOpportunity) -> float:
        """리스크를 고려한 거래 금액(USDT) 계산"""
        base_amount = self.config.max_trade_usdt

        if opp.net_profit_pct < self.config.min_profit_pct * 2:
            base_amount *= 0.5

        max_by_volume = min(opp.buy_volume, opp.sell_volume) * opp.buy_price_usdt * 0.01
        amount = min(base_amount, max_by_volume)

        return max(amount, 0)

    def on_trade_start(self, opp: ArbitrageOpportunity):
        """거래 시작 기록"""
        self._active_trades += 1
        trade_key = f"{opp.symbol}:{opp.buy_exchange}→{opp.sell_exchange}"
        self._last_trade_time[trade_key] = time.time()

    def on_trade_complete(self, opp: ArbitrageOpportunity, actual_profit_usdt: float):
        """거래 완료 기록"""
        self._active_trades = max(0, self._active_trades - 1)
        self._daily_pnl_usdt += actual_profit_usdt
        self._trade_history.append(TradeRecord(
            timestamp=time.time(),
            symbol=opp.symbol,
            buy_exchange=opp.buy_exchange,
            sell_exchange=opp.sell_exchange,
            profit_pct=opp.net_profit_pct,
            amount_usdt=actual_profit_usdt,
        ))
        logger.info("거래 완료: %s (수익: %.4f USDT, 일일 누적: %.4f USDT)",
                     opp.symbol, actual_profit_usdt, self._daily_pnl_usdt)

    def _reset_daily_pnl_if_needed(self):
        import datetime
        today = datetime.date.today().isoformat()
        if self._pnl_date != today:
            if self._pnl_date:
                logger.info("일일 PnL 초기화 (전일: %.4f USDT)", self._daily_pnl_usdt)
            self._daily_pnl_usdt = 0.0
            self._pnl_date = today

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl_usdt

    @property
    def trade_count_today(self) -> int:
        import datetime
        today = datetime.date.today().isoformat()
        cutoff = time.mktime(datetime.date.today().timetuple())
        return sum(1 for t in self._trade_history if t.timestamp >= cutoff)
