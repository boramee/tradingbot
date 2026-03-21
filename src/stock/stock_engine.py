"""주식 자동매매 엔진 (한국투자증권)

코인 봇과의 차이점:
  - 장 운영시간(9:00~15:30)만 매매
  - 정수 수량 주문 (1주 단위)
  - 호가 단위 존재
  - 분봉/일봉 모두 지원
"""

from __future__ import annotations

import datetime
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from src.indicators.technical import TechnicalIndicators
from src.indicators.advanced import AdvancedIndicators
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.combined import CombinedStrategy
from src.utils.telegram_bot import TelegramNotifier
from .kis_client import KISClient

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "rsi": RSIStrategy, "macd": MACDStrategy,
    "bollinger": BollingerStrategy, "combined": CombinedStrategy,
}

MARKET_OPEN = datetime.time(9, 0)
MARKET_CLOSE = datetime.time(15, 20)


@dataclass
class StockPosition:
    code: str
    name: str = ""
    avg_price: int = 0
    quantity: int = 0
    highest_price: int = 0
    entry_atr: float = 0
    partial_sold: bool = False

    @property
    def is_holding(self) -> bool:
        return self.quantity > 0

    def update_highest(self, price: int):
        if price > self.highest_price:
            self.highest_price = price


@dataclass
class StockTradeLog:
    timestamp: float
    code: str
    side: str
    price: int
    quantity: int
    reason: str
    pnl_pct: float = 0.0


class StockEngine:
    """한국 주식 자동매매"""

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        account_no: str = "",
        account_prod: str = "01",
        is_virtual: bool = True,
        stock_code: str = "005930",
        strategy_name: str = "combined",
        invest_ratio: float = 0.1,
        max_invest_krw: int = 500_000,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 3.0,
        trailing_pct: float = 1.5,
        atr_stop_multiplier: float = 2.0,
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.stock_code = stock_code
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_multiplier = atr_stop_multiplier

        self.kis = KISClient(app_key, app_secret, account_no, account_prod, is_virtual)
        self.indicators = TechnicalIndicators()
        self.adv = AdvancedIndicators()
        self.strategy = STRATEGY_MAP.get(strategy_name.lower(), CombinedStrategy)()
        self.position = StockPosition(code=stock_code)
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.trade_logs: List[StockTradeLog] = []
        self.running = False

        self._daily_trades = 0
        self._max_daily_trades = 10
        self._consecutive_losses = 0
        self._cooldown_until: float = 0
        self._last_trade_time: float = 0
        self._last_stop_time: float = 0
        self._min_trade_interval = 120
        self._stop_lockout = 300
        self._stock_name = ""

    # ── 장 시간 확인 ──

    @staticmethod
    def is_market_open() -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        return MARKET_OPEN <= now.time() <= MARKET_CLOSE

    # ── 데이터 수집 ──

    def _fetch_data(self) -> Optional[pd.DataFrame]:
        df = self.kis.get_ohlcv(self.stock_code, period="D", count=100)
        if df is not None and len(df) >= 30:
            return df

        df = self.kis.get_minute_ohlcv(self.stock_code)
        return df

    def _get_price(self) -> int:
        info = self.kis.get_current_price(self.stock_code)
        if info:
            self._stock_name = info.get("name", self._stock_name)
            return info["price"]
        return 0

    # ── 매매 실행 ──

    def _buy(self, reason: str, current_atr: float = 0) -> bool:
        balance = self.kis.get_balance()
        if not balance:
            return False

        cash = balance["cash"]
        invest = min(int(cash * self.invest_ratio), self.max_invest_krw)
        price = self._get_price()

        if price <= 0:
            return False

        qty = invest // price
        if qty <= 0:
            self.telegram.send(
                "<b>⚠️ 매수 불가</b>\n종목: %s %s\n잔고: %s원\n주가: %s원\n사유: 1주도 매수 불가"
                % (self.stock_code, self._stock_name,
                   "{:,}".format(cash), "{:,}".format(price))
            )
            return False

        result = self.kis.buy(self.stock_code, qty)
        if result and result.get("success"):
            self.position.avg_price = price
            self.position.quantity = qty
            self.position.highest_price = price
            self.position.entry_atr = current_atr
            self.position.partial_sold = False
            self._daily_trades += 1
            self._last_trade_time = time.time()
            self.trade_logs.append(StockTradeLog(
                time.time(), self.stock_code, "BUY", price, qty, reason))
            logger.info("[매수] %s %s | %d주 × %s원 = %s원 | %s",
                        self.stock_code, self._stock_name, qty,
                        "{:,}".format(price), "{:,}".format(qty * price), reason)
            self.telegram.notify_buy(
                "%s %s" % (self.stock_code, self._stock_name),
                price, qty * price, reason)
            return True
        else:
            error = result.get("error", "") if result else "알 수 없음"
            logger.error("[매수 실패] %s: %s", self.stock_code, error)
            self.telegram.notify_error("매수 실패: %s\n%s %s" % (error, self.stock_code, self._stock_name))
        return False

    def _sell(self, reason: str, partial: bool = False) -> bool:
        balance = self.kis.get_balance()
        if not balance:
            return False

        holding = None
        for h in balance["holdings"]:
            if h["code"] == self.stock_code:
                holding = h
                break

        if not holding or holding["quantity"] <= 0:
            return False

        qty = holding["quantity"]
        if partial:
            qty = max(1, qty // 2)

        price = self._get_price()
        pnl_pct = (price - self.position.avg_price) / self.position.avg_price * 100 if self.position.avg_price > 0 else 0

        result = self.kis.sell(self.stock_code, qty)
        if result and result.get("success"):
            tag = "[분할매도]" if partial else "[매도]"
            self._daily_trades += 1
            self._last_trade_time = time.time()
            self.trade_logs.append(StockTradeLog(
                time.time(), self.stock_code, "SELL", price, qty, reason, pnl_pct))
            logger.info("%s %s %s | %d주 × %s원 | 수익: %+.2f%% | %s",
                        tag, self.stock_code, self._stock_name, qty,
                        "{:,}".format(price), pnl_pct, reason)
            self.telegram.notify_sell(
                "%s %s" % (self.stock_code, self._stock_name),
                price, pnl_pct, tag + " " + reason)

            if partial:
                self.position.quantity = holding["quantity"] - qty
                self.position.partial_sold = True
            else:
                if pnl_pct < 0:
                    self._consecutive_losses += 1
                    if self._consecutive_losses >= 3:
                        self._cooldown_until = time.time() + 600
                        self.telegram.send(
                            "<b>⏸ 쿨다운</b>\n%d연속 손실 → 10분 대기" % self._consecutive_losses)
                else:
                    self._consecutive_losses = 0
                self.position = StockPosition(code=self.stock_code)
            return True
        return False

    # ── 손절/익절 ──

    def _check_stop_loss(self, price: int) -> bool:
        if self.position.avg_price <= 0:
            return False
        if self.position.entry_atr > 0:
            stop = self.position.avg_price - self.position.entry_atr * self.atr_stop_multiplier
            return price <= stop
        loss = (self.position.avg_price - price) / self.position.avg_price * 100
        return loss >= self.stop_loss_pct

    def _check_trailing(self, price: int) -> bool:
        if self.position.avg_price <= 0 or self.position.highest_price <= 0:
            return False
        gain = (price - self.position.avg_price) / self.position.avg_price * 100
        if gain < self.take_profit_pct:
            return False
        drop = (self.position.highest_price - price) / self.position.highest_price * 100
        return drop >= self.trailing_pct

    # ── 메인 사이클 ──

    def run_once(self):
        if not self.is_market_open():
            return

        df = self._fetch_data()
        if df is None or len(df) < 20:
            return

        df = self.indicators.add_all(df)
        price = self._get_price()
        if price <= 0:
            return

        # 잔고 동기화
        if self.kis.is_authenticated:
            balance = self.kis.get_balance()
            if balance:
                for h in balance["holdings"]:
                    if h["code"] == self.stock_code:
                        self.position.quantity = h["quantity"]
                        self.position.avg_price = h["avg_price"]
                        break

        is_holding = self.position.quantity > 0 and self.position.avg_price > 0

        if is_holding:
            self.position.update_highest(price)
            gain_pct = (price - self.position.avg_price) / self.position.avg_price * 100

            if self._check_stop_loss(price):
                loss = (self.position.avg_price - price) / self.position.avg_price * 100
                self.telegram.notify_stop_loss(
                    "%s %s" % (self.stock_code, self._stock_name), price, loss)
                self._sell("손절 (%.1f%%)" % loss)
                self._last_stop_time = time.time()
                return

            partial_trigger = self.take_profit_pct * 0.6
            if not self.position.partial_sold and gain_pct >= partial_trigger:
                self._sell("분할익절 (+%.1f%%)" % gain_pct, partial=True)
                return

            if self._check_trailing(price):
                drop = (self.position.highest_price - price) / self.position.highest_price * 100
                self.telegram.notify_take_profit(
                    "%s %s" % (self.stock_code, self._stock_name), price, gain_pct)
                self._sell("트레일링 익절 (최고:%s, 하락:%.1f%%)" % ("{:,}".format(self.position.highest_price), drop))
                return

        # 쿨다운
        now = time.time()
        if now < self._cooldown_until:
            return

        sig = self.strategy.analyze(df)
        logger.debug("[%s] 가격: %s | %s (%.0f%%) | %s",
                     self.stock_code, "{:,}".format(price),
                     sig.signal.value, sig.confidence * 100, sig.reason)

        if not sig.is_actionable:
            return

        if sig.signal == Signal.BUY and not is_holding:
            if now - self._last_trade_time < self._min_trade_interval:
                return
            if now - self._last_stop_time < self._stop_lockout:
                return
            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
            self._buy(sig.reason, current_atr=atr)

        elif sig.signal == Signal.SELL and is_holding:
            self._sell(sig.reason)

    def start(self, poll_sec: int = 10):
        self.running = True

        def _stop(signum, frame):
            logger.info("종료 시그널 수신...")
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        mode = "모의투자" if self.kis.is_virtual else "실전"
        if not self.kis.is_authenticated:
            mode = "시뮬레이션 (API 미연결)"

        logger.info("=" * 55)
        logger.info("  주식 자동매매 봇 시작")
        logger.info("  종목: %s %s", self.stock_code, self._stock_name)
        logger.info("  전략: %s | 모드: %s", self.strategy.name, mode)
        logger.info("  투자비율: %.0f%% | 최대: %s원",
                     self.invest_ratio * 100, "{:,}".format(self.max_invest_krw))
        logger.info("  손절: ATR x%.1f (폴백: %.1f%%)", self.atr_stop_multiplier, self.stop_loss_pct)
        logger.info("  익절: +%.1f%%에서 분할 → +%.1f%%부터 트레일링 %.1f%%",
                     self.take_profit_pct * 0.6, self.take_profit_pct, self.trailing_pct)
        logger.info("  장 운영: 평일 09:00~15:20")
        logger.info("=" * 55)
        self.telegram.notify_start(
            "%s %s" % (self.stock_code, self._stock_name),
            self.strategy.name, mode)

        while self.running:
            try:
                if self.is_market_open():
                    self.run_once()
                else:
                    now = datetime.datetime.now()
                    if now.hour == 15 and now.minute == 21:
                        logger.info("[장 마감] 오늘 거래: %d건", self._daily_trades)
                        self._daily_trades = 0
            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                for _ in range(poll_sec):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("봇 종료 완료")
