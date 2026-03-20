"""리스크 관리 모듈 - 손절/익절, 일일 거래 제한, 포지션 관리"""

import logging
from datetime import datetime, date
from typing import Optional

from config.settings import RiskConfig
from src.strategies.base_strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)


class RiskManager:
    """
    리스크 관리:
    1. 손절/익절 가격 모니터링
    2. 일일 최대 거래 횟수 제한
    3. 최대 포지션 비율 제한
    4. 연속 손실 시 거래 중단
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self._daily_trades: int = 0
        self._trade_date: date = date.today()
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 3

    def _reset_daily_counter(self):
        today = date.today()
        if self._trade_date != today:
            self._daily_trades = 0
            self._trade_date = today
            logger.info("일일 거래 카운터 초기화")

    def check_stop_loss(self, avg_buy_price: float, current_price: float) -> bool:
        """손절 조건 확인"""
        if avg_buy_price <= 0:
            return False
        loss_pct = (avg_buy_price - current_price) / avg_buy_price * 100
        if loss_pct >= self.config.stop_loss_pct:
            logger.warning(
                "손절 조건 충족: 평단가 %.0f → 현재가 %.0f (손실률: %.2f%%)",
                avg_buy_price, current_price, loss_pct,
            )
            return True
        return False

    def check_take_profit(self, avg_buy_price: float, current_price: float) -> bool:
        """익절 조건 확인"""
        if avg_buy_price <= 0:
            return False
        profit_pct = (current_price - avg_buy_price) / avg_buy_price * 100
        if profit_pct >= self.config.take_profit_pct:
            logger.info(
                "익절 조건 충족: 평단가 %.0f → 현재가 %.0f (수익률: %.2f%%)",
                avg_buy_price, current_price, profit_pct,
            )
            return True
        return False

    def can_trade(self) -> bool:
        """거래 가능 여부 확인"""
        self._reset_daily_counter()

        if self._daily_trades >= self.config.max_daily_trades:
            logger.warning("일일 최대 거래 횟수(%d) 도달", self.config.max_daily_trades)
            return False

        if self._consecutive_losses >= self._max_consecutive_losses:
            logger.warning(
                "연속 손실 %d회 - 거래 일시 중단", self._consecutive_losses
            )
            return False

        return True

    def validate_signal(
        self,
        signal: TradeSignal,
        avg_buy_price: float,
        current_price: float,
        krw_balance: float,
        holding_value: float,
    ) -> TradeSignal:
        """전략 신호에 리스크 관리 필터 적용"""
        total_assets = krw_balance + holding_value

        if holding_value > 0:
            if self.check_stop_loss(avg_buy_price, current_price):
                return TradeSignal(
                    Signal.SELL, 1.0,
                    f"[리스크] 손절 실행 (손실률: {(avg_buy_price - current_price) / avg_buy_price * 100:.1f}%)",
                    current_price,
                )
            if self.check_take_profit(avg_buy_price, current_price):
                return TradeSignal(
                    Signal.SELL, 0.9,
                    f"[리스크] 익절 실행 (수익률: {(current_price - avg_buy_price) / avg_buy_price * 100:.1f}%)",
                    current_price,
                )

        if signal.signal == Signal.BUY and total_assets > 0:
            position_ratio = holding_value / total_assets
            if position_ratio >= self.config.max_position_ratio:
                logger.info(
                    "포지션 비율 초과 (%.1f%% >= %.1f%%) - 매수 거부",
                    position_ratio * 100, self.config.max_position_ratio * 100,
                )
                return TradeSignal(Signal.HOLD, 0.0, "[리스크] 포지션 비율 초과", current_price)

        if not self.can_trade() and signal.signal != Signal.HOLD:
            return TradeSignal(Signal.HOLD, 0.0, "[리스크] 거래 제한 상태", current_price)

        return signal

    def record_trade(self, is_profit: bool):
        """거래 결과 기록"""
        self._daily_trades += 1
        if is_profit:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            logger.warning("연속 손실: %d회", self._consecutive_losses)

    def reset_consecutive_losses(self):
        self._consecutive_losses = 0
