"""리스크 관리 모듈

주요 기능:
  - 최대 보유 수량 제한
  - 최대 매수 금액 제한
  - 손절 / 익절 판단
  - 일일 손실 한도 관리
  - 매매 간 쿨다운 적용
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_buy_amount: int = 1_000_000
    max_hold_qty: int = 100
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 5.0
    max_daily_loss: int = 500_000
    cooldown_minutes: int = 5


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    trade_count: int = 0
    last_trade_time: Optional[datetime] = None
    daily_reset_date: str = ""

    def reset_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_reset_date != today:
            self.daily_pnl = 0.0
            self.trade_count = 0
            self.daily_reset_date = today


class RiskManager:
    """매매 리스크 관리"""

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState()

    def can_buy(self, price: int, current_qty: int, cash_balance: int) -> tuple[bool, str]:
        """매수 가능 여부 판단"""
        self.state.reset_if_new_day()

        if current_qty >= self.config.max_hold_qty:
            return False, f"최대 보유수량 초과 ({current_qty}/{self.config.max_hold_qty})"

        if price > cash_balance:
            return False, f"예수금 부족 (필요: {price:,}원, 보유: {cash_balance:,}원)"

        if price > self.config.max_buy_amount:
            return False, f"1회 최대 매수금액 초과 ({price:,} > {self.config.max_buy_amount:,})"

        if abs(self.state.daily_pnl) >= self.config.max_daily_loss:
            return False, f"일일 최대 손실 도달 ({self.state.daily_pnl:,.0f}원)"

        if not self._check_cooldown():
            return False, "쿨다운 시간 미경과"

        return True, "매수 가능"

    def can_sell(self, current_qty: int) -> tuple[bool, str]:
        """매도 가능 여부 판단"""
        if current_qty <= 0:
            return False, "보유 수량 없음"

        if not self._check_cooldown():
            return False, "쿨다운 시간 미경과"

        return True, "매도 가능"

    def check_stop_loss(self, avg_price: float, current_price: int) -> bool:
        """손절 조건 확인"""
        if avg_price <= 0:
            return False
        loss_pct = (avg_price - current_price) / avg_price * 100
        if loss_pct >= self.config.stop_loss_pct:
            logger.warning("손절 신호: 손실률 %.2f%% (기준: %.1f%%)", loss_pct, self.config.stop_loss_pct)
            return True
        return False

    def check_take_profit(self, avg_price: float, current_price: int) -> bool:
        """익절 조건 확인"""
        if avg_price <= 0:
            return False
        gain_pct = (current_price - avg_price) / avg_price * 100
        if gain_pct >= self.config.take_profit_pct:
            logger.info("익절 신호: 수익률 %.2f%% (기준: %.1f%%)", gain_pct, self.config.take_profit_pct)
            return True
        return False

    def calculate_buy_qty(self, price: int, cash_balance: int) -> int:
        """매수 가능 수량 계산"""
        max_amount = min(self.config.max_buy_amount, cash_balance)
        qty = max_amount // price
        remaining = self.config.max_hold_qty
        return min(qty, remaining)

    def record_trade(self, pnl: float = 0.0):
        """매매 기록"""
        self.state.reset_if_new_day()
        self.state.daily_pnl += pnl
        self.state.trade_count += 1
        self.state.last_trade_time = datetime.now()

    def _check_cooldown(self) -> bool:
        if self.state.last_trade_time is None:
            return True
        elapsed = datetime.now() - self.state.last_trade_time
        return elapsed >= timedelta(minutes=self.config.cooldown_minutes)

    def get_pnl_pct(self, avg_price: float, current_price: int) -> float:
        """현재 수익률 계산"""
        if avg_price <= 0:
            return 0.0
        return (current_price - avg_price) / avg_price * 100
