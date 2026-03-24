"""공통 매매 로직 기반 클래스 (주식/코인 공유)

손절, 분할익절, 트레일링 스톱, 승률 추적, 적응형 사이징 등
시장에 관계없이 동일하게 적용되는 수익 관리 로직을 집약.

각 시장 엔진(TraderEngine, StockEngine)이 이 클래스를 상속하여
시장 고유 로직(API, 시간대, 필터 등)만 구현.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BaseTradingEngine:
    """주식/코인 공통 매매 로직 기반 클래스

    서브클래스가 반드시 구현해야 하는 항목:
      - self.fee_rate, self.round_trip_fee_pct
      - self.stop_loss_pct, self.take_profit_pct, self.trailing_pct
      - self.atr_stop_multiplier
    """

    def __init__(
        self,
        stop_loss_pct: float = 3.0,
        take_profit_pct: float = 5.0,
        trailing_pct: float = 2.0,
        atr_stop_multiplier: float = 2.0,
        fee_rate: float = 0.0005,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_multiplier = atr_stop_multiplier
        self.fee_rate = fee_rate
        self.round_trip_fee_pct = fee_rate * 2 * 100

        # 쿨다운 & 거래 제한
        self._daily_trades = 0
        self._max_daily_trades = 10
        self._consecutive_losses = 0
        self._max_consecutive_losses = 3
        self._cooldown_until: float = 0
        self._cooldown_minutes = 15

        self._last_buy_time: float = 0
        self._last_sell_time: float = 0
        self._last_stop_loss_time: float = 0
        self._min_buy_interval = 180
        self._min_rebuy_interval = 300
        self._min_rebuy_after_profit = 120
        self._stop_loss_lockout = 600
        self._last_buy_price: float = 0
        self._last_sell_profitable: bool = False

        # 승률 기반 적응형 포지션 사이징
        self._recent_results: List[float] = []
        self._win_rate_window = 20

        # ADX 적응형 트레일링용 df 캐시
        self._last_df: Optional[pd.DataFrame] = None

    # ── 수익률 계산 ──

    def calc_pnl(self, avg_price: float, current_price: float) -> float:
        """수수료 포함 실수익률 계산 (%)"""
        if avg_price <= 0:
            return 0.0
        gross = (current_price - avg_price) / avg_price * 100
        return gross - self.round_trip_fee_pct

    # ── 손절 ──

    def check_stop_loss(
        self,
        avg_price: float,
        current_price: float,
        entry_atr: float,
        partial_stage: int = 0,
    ) -> bool:
        """v4: 보호적 손절 포함

        - 분할익절 이후: 손익분기 또는 이익보장 라인으로 스톱 이동
        - 분할익절 전: ATR 기반 또는 고정% 손절
        """
        if avg_price <= 0:
            return False

        # 분할익절 후 보호적 스톱
        if partial_stage >= 1:
            if partial_stage >= 2 and entry_atr > 0:
                protect_price = avg_price + entry_atr * 0.5
            else:
                protect_price = avg_price * (1 + self.fee_rate * 2)
            if current_price <= protect_price:
                return True

        # ATR 기반 손절
        if entry_atr > 0:
            stop_price = avg_price - entry_atr * self.atr_stop_multiplier
            return current_price <= stop_price

        # 폴백: 고정 %
        pnl = self.calc_pnl(avg_price, current_price)
        return pnl <= -self.stop_loss_pct

    def get_stop_loss_detail(
        self,
        avg_price: float,
        current_price: float,
        entry_atr: float,
        partial_stage: int = 0,
    ) -> str:
        """손절 상세 사유"""
        if partial_stage >= 1:
            pnl = self.calc_pnl(avg_price, current_price)
            if partial_stage >= 2 and entry_atr > 0:
                return "보호스톱 %d차익절후 (진입가+ATR×0.5, PnL:%+.1f%%)" % (partial_stage, pnl)
            return "손익분기 보호스톱 %d차익절후 (PnL:%+.1f%%)" % (partial_stage, pnl)

        if entry_atr > 0:
            stop_dist = entry_atr * self.atr_stop_multiplier
            stop_price = avg_price - stop_dist
            loss = (avg_price - current_price) / avg_price * 100
            return "ATR 동적손절 (ATR:%.0f x%.1f = 손절가:%.0f, 손실:%.1f%%)" % (
                entry_atr, self.atr_stop_multiplier, stop_price, loss)
        loss = (avg_price - current_price) / avg_price * 100
        return "고정손절 (%.1f%%)" % loss

    # ── 분할익절 트리거 계산 ──

    def get_partial_triggers(self, avg_price: float, entry_atr: float) -> tuple:
        """v4: ATR 기반 동적 분할익절 트리거 반환 (tp1_pct, tp2_pct)"""
        tp = self.take_profit_pct
        if entry_atr > 0 and avg_price > 0:
            atr_tp1 = (entry_atr * 1.5) / avg_price * 100
            atr_tp2 = (entry_atr * 3.0) / avg_price * 100
            return min(tp * 0.6, atr_tp1), min(tp, atr_tp2)
        return tp * 0.6, tp

    # ── 트레일링 스톱 ──

    def get_trail_multiplier(self) -> float:
        """v4: ADX 기반 트레일링 배수 + 거래량 클라이맥스"""
        if self._last_df is None:
            return 1.5

        adx = None
        if "adx" in self._last_df.columns:
            v = self._last_df["adx"].iloc[-1]
            if pd.notna(v):
                adx = float(v)

        vol_ratio = None
        if "vol_ratio" in self._last_df.columns:
            v = self._last_df["vol_ratio"].iloc[-1]
            if pd.notna(v):
                vol_ratio = float(v)

        # 거래량 클라이맥스 → 반전 임박
        if vol_ratio is not None and vol_ratio > 3.0:
            return 0.8

        if adx is None:
            return 1.5
        if adx >= 30:
            return 2.5  # 강한 추세
        if adx >= 20:
            return 1.5  # 보통
        return 1.0      # 약한 추세

    def check_trailing_stop(
        self,
        avg_price: float,
        current_price: float,
        highest_price: float,
        entry_atr: float,
        partial_stage: int = 0,
    ) -> bool:
        """v4: ADX 적응형 트레일링 스톱"""
        if avg_price <= 0 or highest_price <= 0:
            return False

        pnl = self.calc_pnl(avg_price, current_price)
        tp = self.take_profit_pct
        min_pnl = tp * 0.5 if partial_stage >= 2 else tp
        if pnl < min_pnl:
            return False

        trail_mult = self.get_trail_multiplier()

        if entry_atr > 0:
            trail_price = highest_price - entry_atr * trail_mult
            return current_price <= trail_price

        # 폴백: 고정 % (ADX 보정)
        adjusted_trailing = self.trailing_pct * (trail_mult / 1.5)
        drop = (highest_price - current_price) / highest_price * 100
        return drop >= adjusted_trailing

    def get_trailing_detail(
        self,
        avg_price: float,
        current_price: float,
        highest_price: float,
        entry_atr: float,
    ) -> str:
        """트레일링 스톱 상세 사유"""
        gain = (current_price - avg_price) / avg_price * 100
        drop = (highest_price - current_price) / highest_price * 100
        trail_mult = self.get_trail_multiplier()
        if entry_atr > 0:
            trail_dist = entry_atr * trail_mult
            return "ATR트레일링 익절 (수익:+%.1f%%, 최고:%s, ATR×%.1f=%s)" % (
                gain, "{:,.0f}".format(highest_price),
                trail_mult, "{:,.0f}".format(trail_dist))
        return "트레일링 익절 (수익:+%.1f%%, 최고점:%s, 하락:%.1f%%)" % (
            gain, "{:,.0f}".format(highest_price), drop)

    # ── 승률 추적 & 포지션 사이징 ──

    def record_trade_result(self, pnl_pct: float):
        """거래 결과 기록: 승률 추적 + 연속손실 쿨다운"""
        self._recent_results.append(pnl_pct)
        if len(self._recent_results) > self._win_rate_window:
            self._recent_results.pop(0)

        self._last_sell_profitable = pnl_pct > 0

        if pnl_pct < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._max_consecutive_losses:
                self._cooldown_until = time.time() + self._cooldown_minutes * 60
                logger.warning(
                    "[쿨다운] %d연속 손실 → %d분 매매 중지",
                    self._consecutive_losses, self._cooldown_minutes)
        else:
            self._consecutive_losses = 0

    def get_win_rate_multiplier(self) -> float:
        """승률 기반 포지션 크기 보정 (0.6 ~ 1.3)"""
        if len(self._recent_results) < 5:
            return 1.0
        wins = sum(1 for r in self._recent_results if r > 0)
        rate = wins / len(self._recent_results)
        if rate > 0.6:
            return 1.3
        if rate < 0.4:
            return 0.6
        return 1.0

    def get_confidence_multiplier(self, confidence: float) -> float:
        """신뢰도 × 승률 기반 투자금 배수"""
        conf_mult = 0.6 + confidence * 1.2
        wr_mult = self.get_win_rate_multiplier()
        return min(conf_mult * wr_mult, 1.8)

    # ── 쿨다운 ──

    def is_in_cooldown(self) -> bool:
        """연속 손실 쿨다운 확인"""
        if time.time() < self._cooldown_until:
            return True
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            self._cooldown_until = 0
            self._consecutive_losses = 0
            logger.info("[쿨다운 해제] 매매 재개")
        return False

    def check_rebuy_cooldown(self, now: float) -> bool:
        """재진입 쿨다운 체크. True면 매수 차단."""
        if now - self._last_buy_time < self._min_buy_interval:
            return True
        rebuy_wait = self._min_rebuy_after_profit if self._last_sell_profitable else self._min_rebuy_interval
        if now - self._last_sell_time < rebuy_wait:
            return True
        if now - self._last_stop_loss_time < self._stop_loss_lockout:
            return True
        return False
