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
from src.strategies.adaptive import AdaptiveStrategy
from src.utils.telegram_bot import TelegramNotifier
from .kis_client import KISClient
from .scanner import StockScanner

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "rsi": RSIStrategy, "macd": MACDStrategy,
    "bollinger": BollingerStrategy, "combined": CombinedStrategy,
    "adaptive": AdaptiveStrategy,
}

MARKET_OPEN = datetime.time(9, 0)
MARKET_CLOSE = datetime.time(15, 20)
OPEN_SETTLE = datetime.time(9, 5)      # 시초가 안정 (9:00~9:05 관망)
GOLDEN_HOUR_END = datetime.time(10, 0)  # 골든타임 종료
CLOSING_MODE = datetime.time(14, 30)    # 오후 청산 모드 전환


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
        auto_scan: bool = False,
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

        self.auto_scan = auto_scan
        self.kis = KISClient(app_key, app_secret, account_no, account_prod, is_virtual)
        self.indicators = TechnicalIndicators()
        self.adv = AdvancedIndicators()
        self.strategy = STRATEGY_MAP.get(strategy_name.lower(), CombinedStrategy)()
        self.scanner = StockScanner(self.kis)
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
        self._stop_lockout = 900        # 손절 후 15분 재진입 금지
        self._stock_name = ""

        # 수급/지수 캐시
        self._supply_cache: Optional[Dict] = None
        self._supply_cache_time: float = 0
        self._index_cache: Optional[Dict] = None
        self._index_cache_time: float = 0

    # ── 장 시간 확인 ──

    @staticmethod
    def is_market_open() -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        return MARKET_OPEN <= now.time() <= MARKET_CLOSE

    # ── 시간대별 모드 판단 ──

    @staticmethod
    def get_trading_mode() -> str:
        """현재 시간대별 트레이딩 모드
        opening_wait: 9:00~9:05 시초가 안정 대기
        golden_hour:  9:05~10:00 집중 매매 시간
        normal:       10:00~14:30 일반 매매
        closing:      14:30~15:20 신규매수 금지, 청산만
        closed:       장 외 시간
        """
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return "closed"
        t = now.time()
        if t < MARKET_OPEN or t > MARKET_CLOSE:
            return "closed"
        if t < OPEN_SETTLE:
            return "opening_wait"
        if t < GOLDEN_HOUR_END:
            return "golden_hour"
        if t < CLOSING_MODE:
            return "normal"
        return "closing"

    # ── 시장 환경 필터 ──

    def _check_market_conditions(self) -> tuple:
        """시장 환경 체크. (통과 여부, 사유) 반환."""
        now = time.time()

        # 지수 체크 (60초 캐시)
        if now - self._index_cache_time > 60:
            self._index_cache = self.kis.get_index_price("0001")
            self._index_cache_time = now

        if self._index_cache:
            idx_change = self._index_cache.get("change_pct", 0)
            if idx_change <= -1.5:
                return False, "코스피 급락 (%.1f%%)" % idx_change

        return True, ""

    def _check_supply_demand(self) -> tuple:
        """수급 확인. (통과 여부, 사유, 상세) 반환."""
        now = time.time()

        # 수급 캐시 (60초)
        if now - self._supply_cache_time > 60:
            self._supply_cache = self.kis.get_investor_trend(self.stock_code)
            self._supply_cache_time = now

        if not self._supply_cache:
            return True, "", {}

        foreign = self._supply_cache.get("foreign_net", 0)
        institution = self._supply_cache.get("institution_net", 0)

        if foreign < 0 and institution < 0:
            return False, "외국인(%+d) + 기관(%+d) 동반 매도" % (foreign, institution), self._supply_cache

        return True, "", self._supply_cache

    def _check_volume_power(self) -> tuple:
        """체결강도 확인. 100 이상이면 매수세 우세."""
        vp = self.kis.get_volume_power(self.stock_code)
        if vp > 0 and vp < 80:
            return False, "체결강도 약세 (%.0f%%)" % vp
        return True, ""

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
            self.position.code = self.stock_code
            self.position.name = self._stock_name
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
                self.position = StockPosition(code=self.stock_code, name=self._stock_name)
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

    def _sync_position_from_balance(self):
        """잔고 기반으로 현재 포지션을 동기화한다."""
        balance = self.kis.get_balance()
        if not balance:
            return

        holding = None
        for item in balance["holdings"]:
            if item["code"] == self.stock_code:
                holding = item
                break

        if not holding:
            self.position = StockPosition(code=self.stock_code, name=self._stock_name)
            return

        self._stock_name = holding.get("name", self._stock_name) or self._stock_name
        prev_avg_price = self.position.avg_price
        self.position.code = self.stock_code
        self.position.name = self._stock_name
        self.position.quantity = holding["quantity"]
        self.position.avg_price = holding["avg_price"]

        if prev_avg_price != holding["avg_price"]:
            self.position.entry_atr = 0
            self.position.partial_sold = False

        # 재시작/외부 체결 직후에는 최고가가 0이라 트레일링이 비활성화되므로
        # 잔고에서 현재가를 받아 최소 초기값을 채운다.
        if self.position.highest_price <= 0 or prev_avg_price != holding["avg_price"]:
            self.position.highest_price = max(holding.get("current_price", 0), holding["avg_price"])

    # ── 메인 사이클 ──

    def run_once(self):
        mode = self.get_trading_mode()
        if mode == "closed":
            return

        if self.kis.is_authenticated:
            self._sync_position_from_balance()

        is_holding = self.position.quantity > 0 and self.position.avg_price > 0

        # ── 시간대별 매수 필터 ──
        if mode == "opening_wait" and not is_holding:
            logger.debug("[%s] 시초가 안정 대기 (9:00~9:05)", self.stock_code)
            return

        if mode == "closing" and not is_holding:
            logger.debug("[%s] 장마감 모드 - 신규매수 금지", self.stock_code)
            return

        # 자동 스캔 모드는 현재 고정 종목의 신호와 무관하게 스캐너를 먼저 실행한다.
        if self.auto_scan and not is_holding:
            now = time.time()
            if now < self._cooldown_until:
                return
            self._run_auto_scan(now)
            return

        df = self._fetch_data()
        if df is None or len(df) < 20:
            return

        df = self.indicators.add_all(df)
        price = self._get_price()
        if price <= 0:
            return

        # ── 보유 중: 손절/익절 (시간대 무관, 항상 실행) ──
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

            # 14:30 이후: 보유 중이면 청산 (당일 매매 원칙)
            if mode == "closing" and gain_pct > 0:
                self._sell("장마감 전 청산 (+%.1f%%)" % gain_pct)
                return

        # ── 쿨다운 체크 ──
        now = time.time()
        if now < self._cooldown_until:
            return

        # ── 전략 분석 ──
        sig = self.strategy.analyze(df)
        logger.debug("[%s] %s | 가격: %s | %s (%.0f%%) | 모드: %s | %s",
                     self.stock_code, self._stock_name, "{:,}".format(price),
                     sig.signal.value, sig.confidence * 100, mode, sig.reason)

        if not sig.is_actionable:
            return

        # ── 고정 종목 모드: 기존 로직 ──
        if sig.signal == Signal.BUY and not is_holding:
            if not self._pre_buy_checks(now):
                return
            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
            self._buy(sig.reason, current_atr=atr)

        elif sig.signal == Signal.SELL and is_holding:
            self._sell(sig.reason)

    def _pre_buy_checks(self, now: float) -> bool:
        """매수 전 공통 필터 체크"""
        if now - self._last_trade_time < self._min_trade_interval:
            return False
        if now - self._last_stop_time < self._stop_lockout:
            return False

        mkt_ok, mkt_reason = self._check_market_conditions()
        if not mkt_ok:
            logger.info("[매수 차단] %s", mkt_reason)
            self.telegram.send("<b>🚫 매수 차단</b>\n사유: %s" % mkt_reason)
            return False

        if self.kis.is_authenticated:
            sup_ok, sup_reason, _ = self._check_supply_demand()
            if not sup_ok:
                logger.info("[매수 차단] %s - %s", self.stock_code, sup_reason)
                return False
            vp_ok, vp_reason = self._check_volume_power()
            if not vp_ok:
                logger.info("[매수 차단] %s - %s", self.stock_code, vp_reason)
                return False

        return True

    def _run_auto_scan(self, now: float):
        """자동 스캔 모드: 스캐너 → 필터 → 전략 확인 → 매수"""
        if not self._pre_buy_checks(now):
            return

        best = self.scanner.get_best()
        if not best:
            return

        # 스캔된 종목의 차트 분석
        df = self.kis.get_ohlcv(best.code, period="D", count=60)
        if df is None or len(df) < 20:
            return

        df = self.indicators.add_all(df)
        sig = self.strategy.analyze(df)

        if sig.signal != Signal.BUY or not sig.is_actionable:
            logger.debug("[스캐너] %s %s 차트 신호 부적합 (%s)", best.code, best.name, sig.reason)
            return

        # 종목 전환
        old_code = self.stock_code
        old_name = self._stock_name
        self.stock_code = best.code
        self._stock_name = best.name

        atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
        scan_reason = "스캐너 선정 (점수:%.0f, %s) + %s" % (best.score, ", ".join(best.reasons), sig.reason)

        if self._buy(scan_reason, current_atr=atr):
            self.scanner.exclude(best.code)
            logger.info("[스캐너] 종목 선정: %s", best.summary())
            if best.sector:
                self.telegram.send(
                    "<b>📡 스캐너 종목 선정</b>\n"
                    "종목: %s %s\n"
                    "점수: %.0f\n"
                    "섹터: %s\n"
                    "사유: %s"
                    % (best.code, best.name, best.score,
                       best.sector or "개별", ", ".join(best.reasons))
                )
        else:
            self.stock_code = old_code
            self._stock_name = old_name

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
        scan_mode = "자동 스캔 (전 종목 탐색)" if self.auto_scan else "고정 종목"
        logger.info("  종목: %s %s (%s)", self.stock_code, self._stock_name, scan_mode)
        logger.info("  전략: %s | 모드: %s", self.strategy.name, mode)
        logger.info("  투자비율: %.0f%% | 최대: %s원",
                     self.invest_ratio * 100, "{:,}".format(self.max_invest_krw))
        logger.info("  손절: ATR x%.1f (폴백: %.1f%%)", self.atr_stop_multiplier, self.stop_loss_pct)
        logger.info("  익절: +%.1f%%에서 분할 → +%.1f%%부터 트레일링 %.1f%%",
                     self.take_profit_pct * 0.6, self.take_profit_pct, self.trailing_pct)
        logger.info("  손절 후 재진입: 15분 금지")
        logger.info("  장 운영: 09:00~09:05 관망 → 09:05~10:00 집중 → 14:30 청산모드")
        logger.info("  필터: 코스피급락차단 + 수급(외인/기관) + 체결강도")
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
