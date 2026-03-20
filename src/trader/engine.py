"""단일 거래소(업비트) 기술적 분석 자동매매 엔진"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pyupbit

from src.indicators.technical import TechnicalIndicators
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.combined import CombinedStrategy

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

    @property
    def is_holding(self) -> bool:
        return self.volume > 0


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
        candle_count: int = 200,
    ):
        self.ticker = ticker
        self.interval = interval
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.candle_count = candle_count

        self._upbit: Optional[pyupbit.Upbit] = None
        if access_key and secret_key:
            self._upbit = pyupbit.Upbit(access_key, secret_key)

        self.indicators = TechnicalIndicators()
        self.strategy = self._make_strategy(strategy_name)
        self.position = Position(ticker=ticker)
        self.trade_logs: List[TradeLog] = []
        self.running = False

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

    def _buy(self, reason: str) -> bool:
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
                self._daily_trades += 1
                self.trade_logs.append(TradeLog(time.time(), "BUY", price, amount, reason))
                logger.info("[매수] %s | %.0f원 투자 | %s", self.ticker, amount, reason)
                return True
            logger.error("[매수 실패] %s", result)
        else:
            logger.info("[시뮬] 매수: %.0f원 | 가격: %.0f | %s", amount, price, reason)
            self.position.avg_price = price
            self.position.volume = amount / price
            self.position.entry_time = time.time()
            self._daily_trades += 1
            self.trade_logs.append(TradeLog(time.time(), "BUY", price, amount, reason))
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
                self.position = Position(ticker=self.ticker)
                return True
            logger.error("[매도 실패] %s", result)
        else:
            self._daily_trades += 1
            self.trade_logs.append(TradeLog(time.time(), "SELL", price, volume * price, reason, pnl_pct))
            logger.info("[시뮬] 매도: 수익률 %+.2f%% | %s", pnl_pct, reason)
            self.position = Position(ticker=self.ticker)
            return True
        return False

    # ── 손절/익절 ──

    def _check_stop_loss(self, current_price: float) -> bool:
        if self.position.avg_price <= 0:
            return False
        loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
        return loss >= self.stop_loss_pct

    def _check_take_profit(self, current_price: float) -> bool:
        if self.position.avg_price <= 0:
            return False
        gain = (current_price - self.position.avg_price) / self.position.avg_price * 100
        return gain >= self.take_profit_pct

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

        # 손절/익절 우선 체크
        if is_holding:
            if self._check_stop_loss(current_price):
                loss = (self.position.avg_price - current_price) / self.position.avg_price * 100
                self._sell("손절 (%.1f%%)" % loss)
                return
            if self._check_take_profit(current_price):
                gain = (current_price - self.position.avg_price) / self.position.avg_price * 100
                self._sell("익절 (%.1f%%)" % gain)
                return

        # 전략 분석
        sig = self.strategy.analyze(df)
        self._log_status(current_price, sig, is_holding)

        if not sig.is_actionable:
            return
        if self._daily_trades >= self._max_daily_trades:
            logger.info("[제한] 일일 최대 거래 횟수 도달 (%d)", self._max_daily_trades)
            return

        if sig.signal == Signal.BUY and not is_holding:
            self._buy(sig.reason)
        elif sig.signal == Signal.SELL and is_holding:
            self._sell(sig.reason)

    def _log_status(self, price: float, sig: TradeSignal, holding: bool):
        hold_str = ""
        if holding and self.position.avg_price > 0:
            pnl = (price - self.position.avg_price) / self.position.avg_price * 100
            hold_str = " | 평단: %s | 수익률: %+.2f%%" % ("{:,.0f}".format(self.position.avg_price), pnl)
        logger.info(
            "[%s] 현재가: %s | 전략: %s | 신호: %s (%.0f%%)%s",
            self.ticker, "{:,.0f}".format(price),
            self.strategy.name, sig.signal.value,
            sig.confidence * 100, hold_str,
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
        logger.info("  손절: %.1f%% | 익절: %.1f%%", self.stop_loss_pct, self.take_profit_pct)
        logger.info("  주기: %d초 | API: %s", poll_sec, "연결됨" if self._upbit else "시뮬레이션")
        logger.info("=" * 55)

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
