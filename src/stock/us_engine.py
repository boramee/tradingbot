"""미국 주식 자동매매 엔진 (한국투자증권 해외주식 API)

장 운영시간: 23:30~06:00 (서머타임 22:30~05:00)
통화: USD
대상: 나스닥/NYSE 대형주 (AAPL, TSLA, NVDA 등)
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
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.strategies.macd import MACDStrategy
from src.strategies.adaptive import AdaptiveStrategy
from src.utils.telegram_bot import TelegramNotifier
from src.utils.safety import KillSwitch, TradeLogger
from src.utils.daily_report import DailyReport
from .kis_client import KISClient

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "macd": MACDStrategy,
    "adaptive": AdaptiveStrategy,
}

US_TOP_STOCKS = {
    "AAPL": ("NAS", "Apple"),
    "MSFT": ("NAS", "Microsoft"),
    "NVDA": ("NAS", "NVIDIA"),
    "TSLA": ("NAS", "Tesla"),
    "AMZN": ("NAS", "Amazon"),
    "GOOGL": ("NAS", "Alphabet"),
    "META": ("NAS", "Meta"),
    "AMD": ("NAS", "AMD"),
    "NFLX": ("NAS", "Netflix"),
    "AVGO": ("NAS", "Broadcom"),
}


@dataclass
class USPosition:
    symbol: str
    exchange: str = "NAS"
    avg_price: float = 0.0
    quantity: int = 0
    highest_price: float = 0.0
    entry_atr: float = 0.0
    partial_sold: bool = False

    @property
    def is_holding(self) -> bool:
        return self.quantity > 0

    def update_highest(self, price: float):
        if price > self.highest_price:
            self.highest_price = price


class USStockEngine:
    """미국 주식 자동매매"""

    FEE_RATE = 0.0025  # 한투 해외주식 수수료 0.25%

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        account_no: str = "",
        account_prod: str = "01",
        is_virtual: bool = True,
        symbols: str = "AAPL,NVDA,TSLA",
        strategy_name: str = "macd",
        invest_ratio: float = 0.3,
        max_invest_usd: float = 500,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 3.0,
        trailing_pct: float = 1.5,
        atr_stop_mult: float = 2.0,
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.symbol_list = [s.strip().upper() for s in symbols.split(",")]
        self.invest_ratio = invest_ratio
        self.max_invest_usd = max_invest_usd
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_mult = atr_stop_mult

        self.kis = KISClient(app_key, app_secret, account_no, account_prod, is_virtual)
        self.indicators = TechnicalIndicators()
        self.strategy = STRATEGY_MAP.get(strategy_name.lower(), MACDStrategy)()
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.kill_switch = KillSwitch(max_daily_loss_pct=3.0)
        self.trade_logger = TradeLogger()
        self.daily_report = DailyReport()
        self.running = False

        self.positions: Dict[str, USPosition] = {}
        self._daily_trades = 0
        self._max_daily_trades = 10
        self._last_trade_time: Dict[str, float] = {}
        self._min_trade_interval = 300
        self._last_report_date = ""

    @staticmethod
    def is_market_open() -> bool:
        """미국 장 오픈 여부 (한국시간 기준)"""
        now = datetime.datetime.now()
        h, m = now.hour, now.minute

        # 서머타임 (3월~11월): 22:30~05:00
        # 비서머타임: 23:30~06:00
        # 간단히 22:30~06:00으로 커버
        if h >= 23 or h < 6:
            return True
        if h == 22 and m >= 30:
            return True
        return False

    def _get_exchange(self, symbol: str) -> str:
        info = US_TOP_STOCKS.get(symbol)
        return info[0] if info else "NAS"

    def _calc_pnl(self, pos: USPosition, price: float) -> float:
        if pos.avg_price <= 0:
            return 0.0
        gross = (price - pos.avg_price) / pos.avg_price * 100
        return gross - self.FEE_RATE * 2 * 100

    def run_once(self):
        if not self.is_market_open():
            return
        if self.kill_switch.is_killed():
            return

        # 보유 종목 손절/익절 체크
        for symbol, pos in list(self.positions.items()):
            if not pos.is_holding:
                continue
            excd = self._get_exchange(symbol)
            info = self.kis.us_get_current_price(symbol, excd)
            if not info:
                continue
            price = info["price"]
            pos.update_highest(price)
            pnl = self._calc_pnl(pos, price)

            # 손절
            if pos.entry_atr > 0:
                stop_price = pos.avg_price - pos.entry_atr * self.atr_stop_mult
                hit_stop = price <= stop_price
            else:
                hit_stop = pnl <= -self.stop_loss_pct

            if hit_stop:
                self._sell(symbol, pos.quantity, "손절 (%.1f%%)" % pnl)
                continue

            # 분할매도
            partial_trigger = self.take_profit_pct * 0.6
            if not pos.partial_sold and pnl >= partial_trigger:
                half = max(1, pos.quantity // 2)
                self._sell(symbol, half, "분할익절 (+%.1f%%)" % pnl, partial=True)
                continue

            # 트레일링
            if pnl >= self.take_profit_pct and pos.highest_price > 0:
                drop = (pos.highest_price - price) / pos.highest_price * 100
                if drop >= self.trailing_pct:
                    self._sell(symbol, pos.quantity, "트레일링 (최고:$%.2f, 하락:%.1f%%)" % (pos.highest_price, drop))
                    continue

        # 매수 탐색
        if self._daily_trades >= self._max_daily_trades:
            return

        for symbol in self.symbol_list:
            if symbol in self.positions and self.positions[symbol].is_holding:
                continue

            now = time.time()
            if now - self._last_trade_time.get(symbol, 0) < self._min_trade_interval:
                continue

            excd = self._get_exchange(symbol)
            df = self.kis.us_get_ohlcv(symbol, excd, count=60)
            if df is None or len(df) < 30:
                continue

            df = self.indicators.add_all(df)
            sig = self.strategy.analyze(df)

            if sig.signal == Signal.BUY and sig.is_actionable:
                atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
                self._buy(symbol, sig.reason, atr)

    def _buy(self, symbol: str, reason: str, atr: float = 0) -> bool:
        excd = self._get_exchange(symbol)
        info = self.kis.us_get_current_price(symbol, excd)
        if not info or info["price"] <= 0:
            return False

        price = info["price"]
        balance = self.kis.us_get_balance()
        cash = balance["cash_usd"] if balance else 10000

        invest = min(cash * self.invest_ratio, self.max_invest_usd)
        qty = int(invest / price)
        if qty <= 0:
            return False

        result = self.kis.us_buy(symbol, qty, excd)
        if result and result.get("success"):
            self.positions[symbol] = USPosition(
                symbol=symbol, exchange=excd, avg_price=price,
                quantity=qty, highest_price=price, entry_atr=atr,
            )
            self._daily_trades += 1
            self._last_trade_time[symbol] = time.time()
            name = US_TOP_STOCKS.get(symbol, ("", symbol))[1]
            logger.info("[매수] %s %s | %d주 × $%.2f = $%.2f | %s",
                        symbol, name, qty, price, qty * price, reason)
            self.telegram.notify_buy(
                "%s %s" % (symbol, name), price, qty * price, reason)
            self.trade_logger.log(
                bot="us_stock", side="BUY", symbol=symbol, exchange=excd,
                price=price, quantity=qty, amount=qty * price, reason=reason)
            return True
        return False

    def _sell(self, symbol: str, qty: int, reason: str, partial: bool = False) -> bool:
        excd = self._get_exchange(symbol)
        info = self.kis.us_get_current_price(symbol, excd)
        if not info:
            return False

        price = info["price"]
        pos = self.positions.get(symbol)
        pnl_pct = self._calc_pnl(pos, price) if pos else 0
        pnl_usd = qty * price * (pnl_pct / 100) if pos else 0

        result = self.kis.us_sell(symbol, qty, excd)
        if result and result.get("success"):
            self._daily_trades += 1
            self._last_trade_time[symbol] = time.time()
            name = US_TOP_STOCKS.get(symbol, ("", symbol))[1]
            tag = "[분할매도]" if partial else "[매도]"
            logger.info("%s %s %s | %d주 × $%.2f | 수익: %+.2f%% | %s",
                        tag, symbol, name, qty, price, pnl_pct, reason)
            self.telegram.notify_sell(
                "%s %s" % (symbol, name), price, pnl_pct, tag + " " + reason)
            self.trade_logger.log(
                bot="us_stock", side="SELL", symbol=symbol, exchange=excd,
                price=price, quantity=qty, amount=qty * price,
                pnl_pct=pnl_pct, pnl_amount=pnl_usd, reason=reason)
            self.kill_switch.record_trade(pnl_usd * 1350)

            if partial and pos:
                pos.quantity -= qty
                pos.partial_sold = True
            elif pos:
                del self.positions[symbol]
            return True
        return False

    def start(self, poll_sec: int = 30):
        self.running = True

        def _stop(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        mode = "모의투자" if self.kis.is_virtual else "실전"
        if not self.kis.is_authenticated:
            mode = "시뮬레이션"

        logger.info("=" * 55)
        logger.info("  미국 주식 자동매매 봇 시작")
        logger.info("  종목: %s", ", ".join(self.symbol_list))
        logger.info("  전략: %s | 모드: %s", self.strategy.name, mode)
        logger.info("  투자비율: %.0f%% | 최대: $%.0f", self.invest_ratio * 100, self.max_invest_usd)
        logger.info("  수수료: %.2f%% (왕복 %.2f%%)", self.FEE_RATE * 100, self.FEE_RATE * 200)
        logger.info("  손절: -%.1f%% | 익절: +%.1f%% → 트레일링 %.1f%%",
                     self.stop_loss_pct, self.take_profit_pct, self.trailing_pct)
        logger.info("  장 시간: 22:30~06:00 (한국시간)")
        logger.info("=" * 55)
        self.telegram.notify_start(
            ", ".join(self.symbol_list), "미국주식 %s" % self.strategy.name, mode)

        while self.running:
            try:
                if self.is_market_open():
                    self.run_once()
                self._send_daily_report_if_needed()
            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                for _ in range(poll_sec):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("봇 종료")

    def _send_daily_report_if_needed(self):
        import datetime as dt
        today = dt.date.today().isoformat()
        if self._last_report_date == today:
            return
        if not self._last_report_date:
            self._last_report_date = today
            return
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        report = self.daily_report.generate(yesterday)
        self.telegram.send(report)
        self._last_report_date = today
