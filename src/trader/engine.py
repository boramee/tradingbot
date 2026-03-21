"""단일 거래소(업비트) 기술적 분석 자동매매 엔진"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import pyupbit

from src.indicators.technical import TechnicalIndicators
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.combined import CombinedStrategy
from src.utils.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "combined": CombinedStrategy,
}


@dataclass
class Position:
    ticker: str
    avg_price: float = 0.0
    volume: float = 0.0
    entry_time: float = 0.0
    highest_price: float = 0.0  # 보유 중 최고가 (트레일링 스톱용)
    entry_atr: float = 0.0      # 진입 시점 ATR (동적 손절용)

    @property
    def is_holding(self) -> bool:
        return self.volume > 0

    def update_highest(self, current_price: float):
        if current_price > self.highest_price:
            self.highest_price = current_price


@dataclass
class TradeLog:
    timestamp: float
    side: str
    price: float
    amount: float
    reason: str
    pnl_pct: float = 0.0


class TraderEngine:
    """
    업비트 단일 거래소 자동매매.

    사이클:
      1. OHLCV 데이터 수집
      2. 기술적 지표 계산
      3. 전략 분석 → 매매 신호
      4. 손절/익절 체크
      5. 주문 실행
    """

    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        ticker: str = "KRW-BTC",
        strategy_name: str = "combined",
        interval: str = "minute60",
        invest_ratio: float = 0.1,
        max_invest_krw: float = 100_000,
        stop_loss_pct: float = 3.0,
        take_profit_pct: float = 5.0,
        trailing_pct: float = 2.0,
        atr_stop_multiplier: float = 2.0,
        candle_count: int = 200,
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.ticker = ticker
        self.interval = interval
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_multiplier = atr_stop_multiplier
        self.candle_count = candle_count

        self._upbit: Optional[pyupbit.Upbit] = None
        if access_key and secret_key:
            self._upbit = pyupbit.Upbit(access_key, secret_key)

        self.indicators = TechnicalIndicators()
        self.strategy = self._make_strategy(strategy_name)
        self.position = Position(ticker=ticker)
        self.trade_logs: List[TradeLog] = []
        self.running = False
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)

        self._daily_trades = 0
        self._max_daily_trades = 10

    def _make_strategy(self, name: str) -> BaseStrategy:
        cls = STRATEGY_MAP.get(name.lower(), CombinedStrategy)
        return cls()

    # ── 데이터 수집 ──

    def _fetch_ohlcv(self):
        df = pyupbit.get_ohlcv(self.ticker, interval=self.interval, count=self.candle_count)
        if df is not None and not df.empty:
            df.columns = ["open", "high", "low", "close", "volume", "value"]
            return df
        return None

    def _get_current_price(self) -> float:
        p = pyupbit.get_current_price(self.ticker)
        return float(p) if p else 0.0

    def _get_krw_balance(self) -> float:
        if not self._upbit:
            return 0.0
        b = self._upbit.get_balance("KRW")
        return float(b) if b else 0.0

    def _get_coin_balance(self) -> float:
        if not self._upbit:
            return 0.0
        currency = self.ticker.split("-")[1]
        b = self._upbit.get_balance(currency)
        return float(b) if b else 0.0

    def _get_avg_buy_price(self) -> float:
        if not self._upbit:
            return 0.0
        currency = self.ticker.split("-")[1]
        p = self._upbit.get_avg_buy_price(currency)
        return float(p) if p else 0.0

    # ── 매매 실행 ──

    def _buy(self, reason: str, current_atr: float = 0.0) -> bool:
        krw = self._get_krw_balance()
        amount = min(krw * self.invest_ratio, self.max_invest_krw)
        if amount < 5000:
            logger.info("[매수 불가] 잔고 부족: %.0f원", krw)
            return False

        price = self._get_current_price()
        if self._upbit:
            result = self._upbit.buy_market_order(self.ticker, amount)
            if result and "error" not in result:
                self.position.avg_price = price
                self.position.volume = amount / price
                self.position.entry_time = time.time()
                self.position.highest_price = price
                self.position.entry_atr = current_atr
                self._daily_trades += 1
                self.trade_logs.append(TradeLog(time.time(), "BUY", price, amount, reason))
                atr_info = " (ATR:%.0f)" % current_atr if current_atr > 0 else ""
                logger.info("[매수] %s | %.0f원 투자%s | %s", self.ticker, amount, atr_info, reason)
                self.telegram.notify_buy(self.ticker, price, amount, reason)
                return True
            logger.error("[매수 실패] %s", result)
        else:
            logger.info("[시뮬] 매수: %.0f원 | 가격: %.0f | %s", amount, price, reason)
            self.position.avg_price = price
            self.position.volume = amount / price
            self.position.entry_time = time.time()
            self.position.highest_price = price
            self.position.entry_atr = current_atr
            self._daily_trades += 1
            self.trade_logs.append(TradeLog(time.time(), "BUY", price, amount, reason))
            self.telegram.notify_buy(self.ticker, price, amount, "[시뮬] " + reason)
            return True
        return False

    def _sell(self, reason: str) -> bool:
        volume = self._get_coin_balance() if self._upbit else self.position.volume
        if volume <= 0:
            return False

        price = self._get_current_price()
        pnl_pct = (price - self.position.avg_price) / self.position.avg_price * 100 if self.position.avg_price > 0 else 0

        if self._upbit:
            result = self._upbit.sell_market_order(self.ticker, volume)
            if result and "error" not in result:
                self._daily_trades += 1
                self.trade_logs.append(TradeLog(time.time(), "SELL", price, volume * price, reason, pnl_pct))
                logger.info("[매도] %s | 수익률: %+.2f%% | %s", self.ticker, pnl_pct, reason)
                self.telegram.notify_sell(self.ticker, price, pnl_pct, reason)
                self.position = Position(ticker=self.ticker)
                return True
            logger.error("[매도 실패] %s", result)
        else:
            self._daily_trades += 1
            self.trade_logs.append(TradeLog(time.time(), "SELL", price, volume * price, reason, pnl_pct))
            logger.info("[시뮬] 매도: 수익률 %+.2f%% | %s", pnl_pct, reason)
            self.telegram.notify_sell(self.ticker, price, pnl_pct, "[시뮬] " + reason)
            self.position = Position(ticker=self.ticker)
            return True
        return False

    # ── 손절/익절 ──

    def _check_stop_loss(self, current_price: float) -> bool:
        """ATR 기반 동적 손절. ATR 없으면 고정 %로 폴백."""
        if self.position.avg_price <= 0:
            return False

        if self.position.entry_atr > 0:
            stop_distance = self.position.entry_atr * self.atr_stop_multiplier
            stop_price = self.position.avg_price - stop_distance
            return current_price <= stop_price

        loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
        return loss >= self.stop_loss_pct

    def _check_trailing_stop(self, current_price: float) -> bool:
        """트레일링 스톱: 최고점 대비 N% 하락 시 익절.
        먼저 take_profit_pct 이상 수익이 나야 활성화됨."""
        if self.position.avg_price <= 0 or self.position.highest_price <= 0:
            return False

        gain_from_entry = (current_price - self.position.avg_price) / self.position.avg_price * 100
        if gain_from_entry < self.take_profit_pct:
            return False

        drop_from_high = (self.position.highest_price - current_price) / self.position.highest_price * 100
        return drop_from_high >= self.trailing_pct

    def _get_stop_loss_detail(self, current_price: float) -> str:
        """손절 상세 사유"""
        if self.position.entry_atr > 0:
            stop_dist = self.position.entry_atr * self.atr_stop_multiplier
            stop_price = self.position.avg_price - stop_dist
            loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
            return "ATR 동적손절 (ATR:%.0f x%.1f = 손절가:%.0f, 손실:%.1f%%)" % (
                self.position.entry_atr, self.atr_stop_multiplier, stop_price, loss)
        loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
        return "고정손절 (%.1f%%)" % loss

    def _get_trailing_detail(self, current_price: float) -> str:
        """트레일링 스톱 상세 사유"""
        gain = (current_price - self.position.avg_price) / self.position.avg_price * 100
        drop = (self.position.highest_price - current_price) / self.position.highest_price * 100
        return "트레일링 익절 (수익:+%.1f%%, 최고점:%s, 하락:%.1f%%)" % (
            gain, "{:,.0f}".format(self.position.highest_price), drop)

    # ── 메인 사이클 ──

    def run_once(self):
        """한 사이클 실행"""
        df = self._fetch_ohlcv()
        if df is None:
            logger.warning("데이터 조회 실패")
            return

        df = self.indicators.add_all(df)
        current_price = self._get_current_price()

        # 포지션 동기화 (API 키 있을 때)
        if self._upbit:
            self.position.volume = self._get_coin_balance()
            self.position.avg_price = self._get_avg_buy_price()

        is_holding = self.position.volume > 0 and self.position.avg_price > 0

        # 최고가 갱신 + 손절/익절 체크
        if is_holding:
            self.position.update_highest(current_price)

            if self._check_stop_loss(current_price):
                detail = self._get_stop_loss_detail(current_price)
                loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
                self.telegram.notify_stop_loss(self.ticker, current_price, loss)
                self._sell(detail)
                return

            if self._check_trailing_stop(current_price):
                detail = self._get_trailing_detail(current_price)
                gain = (current_price - self.position.avg_price) / self.position.avg_price * 100
                self.telegram.notify_take_profit(self.ticker, current_price, gain)
                self._sell(detail)
                return

        # 전략 분석
        sig = self.strategy.analyze(df)
        self._log_status(current_price, sig, is_holding, df)

        if not sig.is_actionable:
            return
        if self._daily_trades >= self._max_daily_trades:
            logger.info("[제한] 일일 최대 거래 횟수 도달 (%d)", self._max_daily_trades)
            return

        if sig.signal == Signal.BUY and not is_holding:
            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
            self._buy(sig.reason, current_atr=atr)
        elif sig.signal == Signal.SELL and is_holding:
            self._sell(sig.reason)

    def _log_status(self, price: float, sig: TradeSignal, holding: bool, df=None):
        extra = ""
        if holding and self.position.avg_price > 0:
            pnl = (price - self.position.avg_price) / self.position.avg_price * 100
            trail = ""
            if self.position.highest_price > 0:
                trail = " | 최고: %s" % "{:,.0f}".format(self.position.highest_price)
            extra = " | 평단: %s | 수익: %+.2f%%%s" % (
                "{:,.0f}".format(self.position.avg_price), pnl, trail)

        adx_str = ""
        if df is not None:
            adx_val = df["adx"].iloc[-1] if "adx" in df.columns and pd.notna(df["adx"].iloc[-1]) else None
            if adx_val is not None:
                trend = "추세" if adx_val >= 20 else "횡보"
                adx_str = " | ADX:%.0f(%s)" % (adx_val, trend)

        logger.info(
            "[%s] 가격: %s%s | %s (%.0f%%)%s | %s",
            self.ticker, "{:,.0f}".format(price), adx_str,
            sig.signal.value, sig.confidence * 100,
            extra, sig.reason,
        )

    def start(self, poll_sec: int = 60):
        """무한 루프 실행"""
        self.running = True

        def _stop(signum, frame):
            logger.info("종료 시그널 수신...")
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        logger.info("=" * 55)
        logger.info("  기술적 분석 자동매매 봇 시작")
        logger.info("  대상: %s | 전략: %s", self.ticker, self.strategy.name)
        logger.info("  투자비율: %.0f%% | 최대: %s원",
                     self.invest_ratio * 100, "{:,.0f}".format(self.max_invest_krw))
        logger.info("  손절: ATR x%.1f (폴백: %.1f%%)", self.atr_stop_multiplier, self.stop_loss_pct)
        logger.info("  익절: +%.1f%% 도달 후 트레일링 %.1f%%", self.take_profit_pct, self.trailing_pct)
        mode = "실거래" if self._upbit else "시뮬레이션"
        logger.info("  주기: %d초 | API: %s", poll_sec, mode)
        logger.info("=" * 55)
        self.telegram.notify_start(self.ticker, self.strategy.name, mode)

        while self.running:
            try:
                self.run_once()
            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                for _ in range(poll_sec):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("봇 종료 완료")
