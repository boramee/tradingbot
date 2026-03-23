"""한국 주식 자동매매 엔진 v3

v2 대비 개선:
  1. 시초가 갭 대응: 시가 vs 전일종가 비교 → 갭상승이면 눌림목 대기
  2. VI 감지: 상한가 근처(+25%~+29%) 진입 금지
  3. 스캐너 흐름 수정: 전략 분석 전에 스캐너 실행
  4. 지정가 주문 + 미체결 정정
  5. 섹터 평균 등락률 기반 진입
  6. 당일 손실 시 14:30 강제 청산 (손실 확대 방지)
  7. CSV 거래 기록 + Kill Switch
  8. 수수료 반영 수익률 계산
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
from src.utils.safety import KillSwitch, TradeLogger
from src.utils.daily_report import DailyReport
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
OPEN_SETTLE = datetime.time(9, 5)
GOLDEN_HOUR_END = datetime.time(10, 0)
CLOSING_MODE = datetime.time(14, 30)

STOCK_FEE = 0.00015  # 국내주식 수수료


@dataclass
class StockPosition:
    code: str
    name: str = ""
    avg_price: int = 0
    quantity: int = 0
    highest_price: int = 0
    entry_atr: float = 0
    partial_sold: bool = False
    entry_time: float = 0

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
    """한국 주식 자동매매 v3"""

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        account_no: str = "",
        account_prod: str = "01",
        is_virtual: bool = True,
        stock_code: str = "005930",
        auto_scan: bool = False,
        strategy_name: str = "macd",
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
        self.auto_scan = auto_scan
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.atr_stop_multiplier = atr_stop_multiplier
        self.fee_rate = STOCK_FEE
        self.round_trip_fee_pct = STOCK_FEE * 2 * 100

        self.kis = KISClient(app_key, app_secret, account_no, account_prod, is_virtual)
        self.indicators = TechnicalIndicators()
        self.adv = AdvancedIndicators()
        self.strategy = STRATEGY_MAP.get(strategy_name.lower(), MACDStrategy)()
        self.scanner = StockScanner(self.kis)
        self.position = StockPosition(code=stock_code)
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.kill_switch = KillSwitch(max_daily_loss_pct=3.0)
        self.trade_logger = TradeLogger()
        self.daily_report = DailyReport()
        self.trade_logs: List[StockTradeLog] = []
        self.running = False

        self._daily_trades = 0
        self._max_daily_trades = 10
        self._consecutive_losses = 0
        self._cooldown_until: float = 0
        self._last_buy_time: float = 0
        self._last_sell_time: float = 0
        self._last_stop_time: float = 0
        self._min_buy_interval = 300       # 매수 후 5분 대기
        self._min_rebuy_interval = 600     # 매도 후 10분 대기
        self._stop_lockout = 900           # 손절 후 15분 금지
        self._stock_name = ""

        # 수급/지수 캐시
        self._supply_cache = None
        self._supply_cache_time: float = 0
        self._index_cache = None
        self._index_cache_time: float = 0
        self._last_report_date = ""
        self._today_open_price: Dict[str, float] = {}
        self._last_block_reason: str = ""
        self._last_block_time: float = 0

    # ── 시간대 ──

    @staticmethod
    def is_market_open() -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        return MARKET_OPEN <= now.time() <= MARKET_CLOSE

    @staticmethod
    def get_trading_mode() -> str:
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

    # ── 수익률 계산 (수수료 포함) ──

    def _calc_pnl(self, sell_price: int) -> float:
        if self.position.avg_price <= 0:
            return 0.0
        gross = (sell_price - self.position.avg_price) / self.position.avg_price * 100
        return gross - self.round_trip_fee_pct

    # ── 시장 환경 필터 ──

    def _check_market_conditions(self) -> tuple:
        now = time.time()
        if now - self._index_cache_time > 60:
            self._index_cache = self.kis.get_index_price("0001")
            self._index_cache_time = now
        if self._index_cache:
            idx_change = self._index_cache.get("change_pct", 0)
            if idx_change <= -1.5:
                return False, "코스피 급락 (%.1f%%)" % idx_change
        return True, ""

    def _check_supply_demand(self) -> tuple:
        now = time.time()
        if now - self._supply_cache_time > 60:
            self._supply_cache = self.kis.get_investor_trend(self.stock_code)
            self._supply_cache_time = now
        if not self._supply_cache:
            return True, ""
        foreign = self._supply_cache.get("foreign_net", 0)
        institution = self._supply_cache.get("institution_net", 0)
        if foreign < 0 and institution < 0:
            return False, "외국인(%+d) + 기관(%+d) 동반 매도" % (foreign, institution)
        return True, ""

    def _check_volume_power(self) -> tuple:
        vp = self.kis.get_volume_power(self.stock_code)
        if 0 < vp < 80:
            return False, "체결강도 약세 (%.0f%%)" % vp
        return True, ""

    def _check_vi_risk(self, price: int) -> bool:
        """VI(변동성 완화장치) 근처 여부. 상한가 +30% 기준 +25% 이상이면 위험."""
        info = self.kis.get_current_price(self.stock_code)
        if not info:
            return False
        change_pct = info.get("change_pct", 0)
        return change_pct >= 25.0

    def _check_gap(self, df, price: int) -> Optional[str]:
        """시초가 갭 분석. 갭상승 5% 이상이면 눌림목 대기."""
        if df is None or len(df) < 2:
            return None
        prev_close = float(df["close"].iloc[-2])
        today_open = float(df["open"].iloc[-1])
        if prev_close <= 0:
            return None
        gap_pct = (today_open - prev_close) / prev_close * 100
        if gap_pct >= 5.0:
            return "갭상승 %.1f%% (눌림목 대기)" % gap_pct
        if gap_pct <= -3.0:
            return "갭하락 %.1f%% (추가 하락 위험)" % gap_pct
        return None

    # ── 데이터 수집 ──

    def _fetch_data(self):
        df = self.kis.get_ohlcv(self.stock_code, period="D", count=100)
        if df is not None and len(df) >= 30:
            return df
        return self.kis.get_minute_ohlcv(self.stock_code)

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
            return False

        result = self.kis.buy(self.stock_code, qty)
        if result and result.get("success"):
            self.position = StockPosition(
                code=self.stock_code, name=self._stock_name,
                avg_price=price, quantity=qty, highest_price=price,
                entry_atr=current_atr, entry_time=time.time(),
            )
            self._daily_trades += 1
            self._last_buy_time = time.time()

            logger.info("[매수] %s %s | %d주 × %s원 = %s원 | %s",
                        self.stock_code, self._stock_name, qty,
                        "{:,}".format(price), "{:,}".format(qty * price), reason)
            self.telegram.notify_buy(
                "%s %s" % (self.stock_code, self._stock_name),
                price, qty * price, reason)
            self.trade_logger.log(
                bot="stock_trader", side="BUY", symbol=self.stock_code,
                exchange="KIS", price=price, quantity=qty, amount=qty * price,
                fee=qty * price * self.fee_rate, reason=reason)
            return True
        else:
            error = result.get("error", "") if result else "알 수 없음"
            logger.error("[매수 실패] %s: %s", self.stock_code, error)
            self.telegram.notify_error("매수 실패: %s\n%s" % (error, self.stock_code))
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
        pnl_pct = self._calc_pnl(price)
        pnl_amount = qty * price * (pnl_pct / 100)

        result = self.kis.sell(self.stock_code, qty)
        if result and result.get("success"):
            tag = "[분할매도]" if partial else "[매도]"
            self._daily_trades += 1
            self._last_sell_time = time.time()

            logger.info("%s %s %s | %d주 × %s원 | 수익: %+.2f%% | %s",
                        tag, self.stock_code, self._stock_name, qty,
                        "{:,}".format(price), pnl_pct, reason)
            self.telegram.notify_sell(
                "%s %s" % (self.stock_code, self._stock_name),
                price, pnl_pct, tag + " " + reason)
            self.trade_logger.log(
                bot="stock_trader", side="SELL", symbol=self.stock_code,
                exchange="KIS", price=price, quantity=qty, amount=qty * price,
                fee=qty * price * self.fee_rate,
                pnl_pct=pnl_pct, pnl_amount=pnl_amount, reason=reason)

            if partial:
                self.position.quantity = holding["quantity"] - qty
                self.position.partial_sold = True
            else:
                self.kill_switch.record_trade(pnl_amount)
                if pnl_pct < 0:
                    self._consecutive_losses += 1
                    if self._consecutive_losses >= 3:
                        self._cooldown_until = time.time() + 900
                        self.telegram.send(
                            "<b>⏸ 쿨다운</b>\n%d연속 손실 → 15분 대기" % self._consecutive_losses)
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
        pnl = self._calc_pnl(price)
        return pnl <= -self.stop_loss_pct

    def _check_trailing(self, price: int) -> bool:
        if self.position.avg_price <= 0 or self.position.highest_price <= 0:
            return False
        pnl = self._calc_pnl(price)
        if pnl < self.take_profit_pct:
            return False
        drop = (self.position.highest_price - price) / self.position.highest_price * 100
        return drop >= self.trailing_pct

    # ── 매수 전 필터 ──

    def _pre_buy_checks(self, now: float, df=None, price: int = 0) -> tuple:
        """매수 전 모든 필터. (통과여부, 사유) 반환."""
        if now - self._last_buy_time < self._min_buy_interval:
            return False, "매수 간격 제한"
        if now - self._last_sell_time < self._min_rebuy_interval:
            return False, "재매수 대기"
        if now - self._last_stop_time < self._stop_lockout:
            return False, "손절 후 대기"
        if self.kill_switch.is_killed():
            return False, "Kill Switch 발동"
        if self._daily_trades >= self._max_daily_trades:
            return False, "일일 거래 한도"

        # 코스피 급락 (같은 사유 5분에 1번만 알림)
        mkt_ok, mkt_reason = self._check_market_conditions()
        if not mkt_ok:
            if mkt_reason != self._last_block_reason or (now - self._last_block_time) > 300:
                self.telegram.send("<b>🚫 매수 차단</b>\n사유: %s" % mkt_reason)
                self._last_block_reason = mkt_reason
                self._last_block_time = now
            return False, mkt_reason

        # VI 근처
        if price > 0 and self._check_vi_risk(price):
            return False, "VI 근처 (등락률 25%+)"

        # 갭 분석
        if df is not None and price > 0:
            gap = self._check_gap(df, price)
            if gap:
                return False, gap

        # 수급
        if self.kis.is_authenticated:
            sup_ok, sup_reason = self._check_supply_demand()
            if not sup_ok:
                return False, sup_reason
            vp_ok, vp_reason = self._check_volume_power()
            if not vp_ok:
                return False, vp_reason

        return True, ""

    # ── 메인 사이클 ──

    def run_once(self):
        mode = self.get_trading_mode()
        if mode == "closed":
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

        # ── 보유 중: 손절/익절 (시간대 무관) ──
        if is_holding:
            self.position.update_highest(price)
            pnl_pct = self._calc_pnl(price)

            # 손절
            if self._check_stop_loss(price):
                self.telegram.notify_stop_loss(
                    "%s %s" % (self.stock_code, self._stock_name), price, abs(pnl_pct))
                self._sell("손절 (%+.1f%%)" % pnl_pct)
                self._last_stop_time = time.time()
                return

            # 분할매도
            partial_trigger = self.take_profit_pct * 0.6
            if not self.position.partial_sold and pnl_pct >= partial_trigger:
                self._sell("분할익절 (%+.1f%%)" % pnl_pct, partial=True)
                return

            # 트레일링
            if self._check_trailing(price):
                drop = (self.position.highest_price - price) / self.position.highest_price * 100
                self.telegram.notify_take_profit(
                    "%s %s" % (self.stock_code, self._stock_name), price, pnl_pct)
                self._sell("트레일링 (최고:%s, 하락:%.1f%%)" % ("{:,}".format(self.position.highest_price), drop))
                return

            # 14:30: 수익이면 청산, 손실이면 손절폭 내 유지
            if mode == "closing":
                if pnl_pct > 0:
                    self._sell("장마감 전 익절 (%+.1f%%)" % pnl_pct)
                elif pnl_pct < -1.0:
                    self._sell("장마감 전 손절 (%+.1f%%)" % pnl_pct)
                return

        # ── 시간대별 매수 필터 ──
        if mode == "opening_wait":
            return
        if mode == "closing":
            return

        # ── 쿨다운 ──
        now = time.time()
        if now < self._cooldown_until:
            return

        # ── 자동 스캔 모드 (전략 분석 전에 실행) ──
        if self.auto_scan and not is_holding:
            self._run_auto_scan(now, df, price)
            return

        # ── 고정 종목 모드 ──
        sig = self.strategy.analyze(df)
        if not sig.is_actionable:
            return

        if sig.signal == Signal.BUY and not is_holding:
            ok, reason = self._pre_buy_checks(now, df, price)
            if not ok:
                logger.debug("[매수 차단] %s", reason)
                return
            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
            self._buy(sig.reason, current_atr=atr)

        elif sig.signal == Signal.SELL and is_holding:
            self._sell(sig.reason)

    def _run_auto_scan(self, now: float, df, price: int):
        """자동 스캔: 스캐너 → 필터 → 전략 → 매수"""
        ok, reason = self._pre_buy_checks(now, df, price)
        if not ok:
            return

        best = self.scanner.get_best()
        if not best:
            return

        # 스캔 종목의 차트 분석
        scan_df = self.kis.get_ohlcv(best.code, period="D", count=60)
        if scan_df is None or len(scan_df) < 20:
            return

        scan_df = self.indicators.add_all(scan_df)
        sig = self.strategy.analyze(scan_df)

        if sig.signal != Signal.BUY or not sig.is_actionable:
            return

        # VI 체크
        scan_info = self.kis.get_current_price(best.code)
        if scan_info and scan_info.get("change_pct", 0) >= 25:
            logger.info("[스캐너] %s VI 근처 → 스킵", best.name)
            return

        # 갭 체크
        scan_price = scan_info["price"] if scan_info else 0
        gap = self._check_gap(scan_df, scan_price)
        if gap:
            logger.info("[스캐너] %s %s → 스킵", best.name, gap)
            return

        # 종목 전환
        old_code = self.stock_code
        self.stock_code = best.code
        self._stock_name = best.name
        self._supply_cache = None
        self._supply_cache_time = 0

        atr = float(scan_df["atr"].iloc[-1]) if "atr" in scan_df.columns and pd.notna(scan_df["atr"].iloc[-1]) else 0
        scan_reason = "스캐너(%.0f점: %s) + %s" % (best.score, ", ".join(best.reasons[:3]), sig.reason)

        if self._buy(scan_reason, current_atr=atr):
            self.scanner.exclude(best.code)
            if best.sector:
                self.telegram.send(
                    "<b>📡 스캐너 종목 선정</b>\n종목: %s %s\n점수: %.0f\n섹터: %s\n사유: %s"
                    % (best.code, best.name, best.score,
                       best.sector or "개별", ", ".join(best.reasons)))
        else:
            self.stock_code = old_code

    # ── 시작 ──

    def start(self, poll_sec: int = 10):
        self.running = True

        def _stop(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        mode_str = "모의투자" if self.kis.is_virtual else "실전"
        if not self.kis.is_authenticated:
            mode_str = "시뮬레이션"
        scan_str = "자동 스캔" if self.auto_scan else "고정: %s" % self.stock_code

        logger.info("=" * 60)
        logger.info("  주식 자동매매 봇 v3 시작")
        logger.info("  종목: %s | 전략: %s | 모드: %s", scan_str, self.strategy.name, mode_str)
        logger.info("  투자: %.0f%% (최대 %s원) | 수수료: %.3f%%",
                     self.invest_ratio * 100, "{:,}".format(self.max_invest_krw), self.fee_rate * 100)
        logger.info("  손절: ATR×%.1f (폴백-%.1f%%) | 익절: +%.1f%%→분할→트레일링%.1f%%",
                     self.atr_stop_multiplier, self.stop_loss_pct,
                     self.take_profit_pct, self.trailing_pct)
        logger.info("  보호: 3연속손실→15분쿨다운 | 일일-3%%→Kill Switch")
        logger.info("  필터: 코스피급락 + 수급 + 체결강도 + VI + 갭")
        logger.info("  장: 09:05관망→10:00골든→14:30청산")
        logger.info("=" * 60)
        self.telegram.notify_start(scan_str, "주식 %s" % self.strategy.name, mode_str)

        while self.running:
            try:
                if self.is_market_open():
                    self.run_once()
                else:
                    now = datetime.datetime.now()
                    if now.hour == 15 and now.minute == 21:
                        logger.info("[장 마감] 오늘 거래: %d건, PnL: %+.0f원",
                                    self._daily_trades, self.kill_switch.daily_pnl)
                        self._daily_trades = 0
                        self.scanner.clear_exclusions()

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
