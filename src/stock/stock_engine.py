"""한국 주식 자동매매 엔진 v4

v3 → v4 변경점:
  - BaseTradingEngine 상속: 코인과 동일한 수익 관리 로직 공유
  - ATR 기반 동적 분할익절 (3단계: 30%+30%+트레일링)
  - ADX 적응형 트레일링 스톱 (추세 강도별 배수 조절)
  - 분할익절 후 보호적 스톱 (손익분기 → 이익보장)
  - 승률 기반 적응형 포지션 사이징
  - 수익/손실 구분 재진입 쿨다운

v3 유지:
  - 시초가 갭/VI/수급/코스피 필터 (주식 고유)
  - 장마감 시간 관리 (주식 고유)
  - 섹터 스캐너 (주식 고유)
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
from src.strategies.fear_greed import FearGreedStrategy
from src.utils.telegram_bot import TelegramNotifier
from src.utils.safety import KillSwitch, TradeLogger
from src.utils.daily_report import DailyReport
from src.trader.base_engine import BaseTradingEngine
from .kis_client import KISClient
from .scanner import StockScanner
from src.intelligence.market_sentiment import MarketSentiment

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "rsi": RSIStrategy, "macd": MACDStrategy,
    "bollinger": BollingerStrategy, "combined": CombinedStrategy,
    "adaptive": AdaptiveStrategy,
    "feargreed": FearGreedStrategy,
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
    partial_sold: bool = False   # 호환용
    partial_stage: int = 0       # v4: 다단계 분할매도 (0=미매도, 1=1차, 2=2차)
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


class StockEngine(BaseTradingEngine):
    """한국 주식 자동매매 v4 (BaseTradingEngine 상속)"""

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
        # 공통 매매 로직 초기화 (손절/익절/트레일링/승률)
        super().__init__(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_pct=trailing_pct,
            atr_stop_multiplier=atr_stop_multiplier,
            fee_rate=STOCK_FEE,
        )

        self.stock_code = stock_code
        self.auto_scan = auto_scan
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self._stock_name = ""

        self.kis = KISClient(app_key, app_secret, account_no, account_prod, is_virtual)
        self.indicators = TechnicalIndicators()
        self.adv = AdvancedIndicators()
        self.strategy = STRATEGY_MAP.get(strategy_name.lower(), MACDStrategy)()
        self.scanner = StockScanner(self.kis)
        self.sentiment = MarketSentiment(self.kis)
        self.position = StockPosition(code=stock_code)
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.kill_switch = KillSwitch(max_daily_loss_pct=3.0)
        self.trade_logger = TradeLogger()
        self.daily_report = DailyReport()
        self.trade_logs: List[StockTradeLog] = []
        self.running = False

        # 주식 고유: 수급/지수 캐시
        self._supply_cache = None
        self._supply_cache_time: float = 0
        self._index_cache = None
        self._index_cache_time: float = 0
        self._last_report_date = ""
        self._today_open_price: Dict[str, float] = {}
        self._last_block_reason: str = ""
        self._last_block_time: float = 0
        self._last_heartbeat: float = 0
        self._last_offhour_heartbeat: float = 0
        self._market_open_notified: str = ""  # 장 시작 알림 날짜
        self._market_close_notified: bool = False

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

    # ── 수익률 계산 (공통 로직 위임) ──

    def _calc_pnl(self, sell_price: int) -> float:
        return self.calc_pnl(self.position.avg_price, sell_price)

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

    # ── 지표 스냅샷 ──

    def _indicator_summary(self, df) -> str:
        """현재 기술 지표 요약 (텔레그램용)"""
        if df is None or df.empty:
            return ""
        row = df.iloc[-1]
        parts = []
        if "rsi" in df.columns and pd.notna(row.get("rsi")):
            rsi = float(row["rsi"])
            tag = "과매도" if rsi < 30 else "과매수" if rsi > 70 else "중립"
            parts.append("RSI: %.1f (%s)" % (rsi, tag))
        if "macd" in df.columns and pd.notna(row.get("macd")):
            macd_val = float(row["macd"])
            hist = float(row["macd_hist"]) if "macd_hist" in df.columns and pd.notna(row.get("macd_hist")) else 0
            parts.append("MACD: %.2f (hist:%.2f)" % (macd_val, hist))
        if "adx" in df.columns and pd.notna(row.get("adx")):
            adx = float(row["adx"])
            tag = "강한추세" if adx > 25 else "횡보"
            parts.append("ADX: %.0f (%s)" % (adx, tag))
        if "bb_pct_b" in df.columns and pd.notna(row.get("bb_pct_b")):
            parts.append("BB%%B: %.2f" % float(row["bb_pct_b"]))
        if "volume_ratio" in df.columns and pd.notna(row.get("volume_ratio")):
            parts.append("거래량: %.1f배" % float(row["volume_ratio"]))
        if "atr" in df.columns and pd.notna(row.get("atr")):
            parts.append("ATR: %.0f" % float(row["atr"]))
        return "\n".join(parts)

    def _market_summary(self) -> str:
        """시장 맥락 요약 (텔레그램용)"""
        parts = []
        idx = self._index_cache
        if idx:
            parts.append("코스피: %+.1f%%" % idx["change_pct"])
        sent = self.sentiment.analyze()
        if sent:
            parts.append("심리: %s(%d점)" % (sent.sentiment, sent.score))
            if sent.vkospi > 0:
                parts.append("VKOSPI: %.1f" % sent.vkospi)
        return " | ".join(parts)

    # ── 매매 실행 ──

    def _buy(self, reason: str, current_atr: float = 0, confidence: float = 0.5) -> bool:
        balance = self.kis.get_balance()
        if not balance:
            return False

        cash = balance["cash"]
        # v4: 신뢰도 × 승률 기반 투자금 조절 (공통 로직)
        size_mult = self.get_confidence_multiplier(confidence)
        invest = min(int(cash * self.invest_ratio * size_mult), self.max_invest_krw)
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

            # 목표가 / 손절가 계산
            tp1, tp2 = self.get_partial_triggers(price, current_atr)
            if current_atr > 0:
                stop_price = int(price - current_atr * self.atr_stop_multiplier)
            else:
                stop_price = int(price * (1 - self.stop_loss_pct / 100))
            tp1_price = int(price * (1 + tp1 / 100))
            tp2_price = int(price * (1 + tp2 / 100))

            outlook = (
                "📊 <b>진입 근거</b>\n%s\n\n"
                "🎯 <b>목표/손절</b>\n"
                "1차 익절: %s원 (+%.1f%%)\n"
                "2차 익절: %s원 (+%.1f%%)\n"
                "손절가: %s원 (%.1f%%)\n"
                "신뢰도: %.0f%%\n\n"
                "📈 <b>기술 지표</b>\n%s\n\n"
                "🌐 <b>시장</b>\n%s"
            ) % (
                self.telegram.escape(reason),
                "{:,}".format(tp1_price), tp1,
                "{:,}".format(tp2_price), tp2,
                "{:,}".format(stop_price),
                (stop_price - price) / price * 100,
                confidence * 100,
                self._indicator_summary(self._last_df) or "N/A",
                self._market_summary() or "N/A",
            )

            self.telegram.send(
                "<b>🟢 매수</b>\n"
                "종목: <code>%s %s</code>\n"
                "가격: %s원 × %d주 = %s원\n\n%s"
                % (self.stock_code, self.telegram.escape(self._stock_name),
                   "{:,}".format(price), qty, "{:,}".format(qty * price),
                   outlook)
            )
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
            # v4: 30% 분할매도 (최소 1주)
            qty = max(1, int(qty * 0.3))

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

            # 보유 기간 계산
            hold_sec = time.time() - self.position.entry_time if self.position.entry_time else 0
            if hold_sec >= 3600:
                hold_str = "%.1f시간" % (hold_sec / 3600)
            else:
                hold_str = "%d분" % max(1, int(hold_sec / 60))

            emoji = "💰" if pnl_pct >= 1.0 else "🟡" if pnl_pct >= 0 else "🔴"
            remaining = holding["quantity"] - qty if partial else 0

            sell_detail = (
                "📊 <b>매도 근거</b>\n%s\n\n"
                "💵 <b>손익</b>\n"
                "매입가: %s원 → 매도가: %s원\n"
                "수익: %+,.0f원 (%+.2f%%)\n"
                "보유: %s\n"
            ) % (
                self.telegram.escape(reason),
                "{:,}".format(self.position.avg_price),
                "{:,}".format(price),
                pnl_amount, pnl_pct,
                hold_str,
            )

            if partial:
                sell_detail += "잔여: %d주 (분할매도)\n" % remaining

            sell_detail += "\n📈 <b>기술 지표</b>\n%s\n\n🌐 <b>시장</b>\n%s" % (
                self._indicator_summary(self._last_df) or "N/A",
                self._market_summary() or "N/A",
            )

            self.telegram.send(
                "<b>%s %s</b>\n"
                "종목: <code>%s %s</code>\n"
                "가격: %s원 × %d주\n"
                "수익률: <b>%+.2f%%</b>\n\n%s"
                % (emoji, "분할매도" if partial else "매도",
                   self.stock_code, self.telegram.escape(self._stock_name),
                   "{:,}".format(price), qty, pnl_pct, sell_detail)
            )
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
                # v4: 공통 승률 추적 + 쿨다운 로직
                self.record_trade_result(pnl_pct)
                if self._consecutive_losses >= self._max_consecutive_losses:
                    self.telegram.send(
                        "<b>⏸ 쿨다운</b>\n%d연속 손실 → %d분 대기"
                        % (self._consecutive_losses, self._cooldown_minutes))
                self.position = StockPosition(code=self.stock_code)
            return True
        return False

    # ── 손절/익절 (v4: 공통 로직 위임) ──

    def _check_stop_loss(self, price: int) -> bool:
        return self.check_stop_loss(
            self.position.avg_price, price,
            self.position.entry_atr, self.position.partial_stage)

    def _check_trailing(self, price: int) -> bool:
        return self.check_trailing_stop(
            self.position.avg_price, price,
            self.position.highest_price, self.position.entry_atr,
            self.position.partial_stage)

    # ── 매수 전 필터 ──

    def _pre_buy_checks(self, now: float, df=None, price: int = 0) -> tuple:
        """매수 전 모든 필터. (통과여부, 사유) 반환."""
        # v4: 공통 쿨다운 로직 (수익/손실 구분 재진입)
        if self.check_rebuy_cooldown(now):
            return False, "쿨다운 대기"
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
        # 하루 1회 자동 학습 (CSV → JSON)
        self.auto_learn_if_needed("stock_trader")

        mode = self.get_trading_mode()
        if mode == "closed":
            return

        # 장 시작 알림 (하루 1회)
        today = datetime.date.today().isoformat()
        if self._market_open_notified != today:
            self._market_open_notified = today
            self._market_close_notified = False
            target = self.stock_code if not self.auto_scan else "자동스캔"
            self.telegram.send("🔔 <b>장 시작</b>\n종목: %s\n모드: %s" % (target, mode))
            logger.info("[장 시작] %s | %s", target, mode)

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
            self._last_df = df  # v4: ADX 적응형 트레일링용
            pnl_pct = self._calc_pnl(price)
            label = "%s %s" % (self.stock_code, self._stock_name)

            # 손절 (v4: 보호적 스톱 포함)
            if self._check_stop_loss(price):
                detail = self.get_stop_loss_detail(
                    self.position.avg_price, price,
                    self.position.entry_atr, self.position.partial_stage)
                self.telegram.notify_stop_loss(label, price, abs(pnl_pct))
                self._sell(detail)
                self._last_stop_loss_time = time.time()
                self._last_sell_time = time.time()
                return

            # v4: 3단계 분할익절 (ATR 동적)
            stage = self.position.partial_stage
            tp1, tp2 = self.get_partial_triggers(self.position.avg_price, self.position.entry_atr)

            if stage == 0 and pnl_pct >= tp1:
                self._sell("1차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (pnl_pct, tp1), partial=True)
                self.position.partial_stage = 1
                return

            if stage == 1 and pnl_pct >= tp2:
                self._sell("2차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (pnl_pct, tp2), partial=True)
                self.position.partial_stage = 2
                return

            # v4: ADX 적응형 트레일링
            if self._check_trailing(price):
                detail = self.get_trailing_detail(
                    self.position.avg_price, price,
                    self.position.highest_price, self.position.entry_atr)
                self.telegram.notify_take_profit(label, price, pnl_pct)
                self._sell(detail)
                self._last_sell_time = time.time()
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

        # ── 쿨다운 (v4: 공통 로직) ──
        now = time.time()
        if self.is_in_cooldown():
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

            # v2: 학습 데이터 기반 신뢰도 보정
            self._last_df = df  # 학습 보정에 현재 지표 필요
            learned_mod = self.get_learned_confidence_modifier()
            if learned_mod != 0:
                sig = TradeSignal(
                    sig.signal,
                    min(1.0, max(0, sig.confidence + learned_mod)),
                    sig.reason + " | 학습보정%+.2f" % learned_mod,
                    sig.price,
                )
                if not sig.is_actionable:
                    logger.debug("[학습필터] 신뢰도 부족 (보정: %+.2f)", learned_mod)
                    return

            if self._buy(sig.reason, current_atr=atr, confidence=sig.confidence):
                self._last_buy_time = now
                self._last_buy_price = price

        elif sig.signal == Signal.SELL and is_holding:
            pnl_before = self._calc_pnl(price)
            if self._sell(sig.reason):
                self._last_sell_time = now
                self._last_sell_profitable = pnl_before > 0

    def _run_auto_scan(self, now: float, df, price: int):
        """자동 스캔: 스캐너 → 필터 → 전략 → 매수"""
        ok, reason = self._pre_buy_checks(now, df, price)
        if not ok:
            logger.debug("[자동스캔] 매수 필터 차단: %s", reason)
            return

        best = self.scanner.get_best()
        if not best:
            logger.debug("[자동스캔] 스캐너 후보 없음")
            return

        logger.info("[자동스캔] 후보: %s %s (%.0f점, %+.1f%%)",
                    best.code, best.name, best.score, best.change_pct)

        # 스캔 종목의 차트 분석
        scan_df = self.kis.get_ohlcv(best.code, period="D", count=60)
        if scan_df is None or len(scan_df) < 20:
            logger.info("[자동스캔] %s OHLCV 데이터 부족 → 스킵", best.name)
            return

        scan_df = self.indicators.add_all(scan_df)
        sig = self.strategy.analyze(scan_df)

        if sig.signal != Signal.BUY or not sig.is_actionable:
            logger.info("[자동스캔] %s 전략 시그널 미발생 (%s) → 스킵", best.name, sig.reason)
            return

        # VI 체크
        scan_info = self.kis.get_current_price(best.code)
        if scan_info and scan_info.get("change_pct", 0) >= 25:
            logger.info("[자동스캔] %s VI 근처 → 스킵", best.name)
            return

        # 갭 체크
        scan_price = scan_info["price"] if scan_info else 0
        gap = self._check_gap(scan_df, scan_price)
        if gap:
            logger.info("[자동스캔] %s %s → 스킵", best.name, gap)
            return

        # 종목 전환
        old_code = self.stock_code
        self.stock_code = best.code
        self._stock_name = best.name
        self._supply_cache = None
        self._supply_cache_time = 0

        atr = float(scan_df["atr"].iloc[-1]) if "atr" in scan_df.columns and pd.notna(scan_df["atr"].iloc[-1]) else 0
        scan_reason = "스캐너(%.0f점: %s) + %s" % (best.score, ", ".join(best.reasons[:3]), sig.reason)

        logger.info("[자동스캔] %s %s 매수 시도 (사유: %s)", best.code, best.name, scan_reason)
        if self._buy(scan_reason, current_atr=atr):
            self.scanner.exclude(best.code)
            if best.sector:
                self.telegram.send(
                    "<b>📡 스캐너 종목 선정</b>\n종목: %s %s\n점수: %.0f\n섹터: %s\n사유: %s"
                    % (best.code, best.name, best.score,
                       best.sector or "개별", ", ".join(best.reasons)))
        else:
            logger.info("[자동스캔] %s 매수 실패 (잔고 부족 가능)", best.name)
            self.stock_code = old_code

    # ── 시작 ──

    def preflight_check(self) -> bool:
        """실전 투입 전 사전점검. 필수 항목 실패 시 False 반환 + 텔레그램 리포트."""
        # (이름, 통과여부, 상세, 필수여부)
        checks = []

        # 1. KIS 인증 (필수)
        if self.kis.is_authenticated:
            checks.append(("KIS 인증", True, "토큰 정상", True))
        else:
            checks.append(("KIS 인증", False, "토큰 없음 — .env에 KIS_APP_KEY/SECRET 확인", True))

        # 2. 잔고 조회 (경고)
        cash = 0
        if self.kis.is_authenticated:
            balance = self.kis.get_balance()
            if balance and balance.get("cash", 0) > 0:
                cash = balance["cash"]
                checks.append(("잔고 조회", True, "%s원" % "{:,}".format(cash), False))
            else:
                checks.append(("잔고 조회", False, "잔고 0원 또는 조회 실패", False))
        else:
            checks.append(("잔고 조회", False, "인증 필요", False))

        # 3. 시세 조회 (필수)
        price = self._get_price()
        if price > 0:
            checks.append(("시세 조회", True, "%s %s: %s원" % (
                self.stock_code, self._stock_name, "{:,}".format(price)), True))
        else:
            checks.append(("시세 조회", False, "%s 시세 조회 실패" % self.stock_code, True))

        # 4. 텔레그램 (경고)
        if self.telegram.enabled:
            checks.append(("텔레그램", True, "활성화", False))
        else:
            checks.append(("텔레그램", False, "비활성 — TELEGRAM_TOKEN/CHAT_ID 확인", False))

        # 5. 투자 가능 여부 (경고 — 스캐너가 저가 종목을 찾을 수 있음)
        if cash > 0 and price > 0:
            max_qty = min(int(cash * self.invest_ratio), self.max_invest_krw) // price
            if max_qty > 0:
                checks.append(("매수 가능", True, "최대 %d주 (약 %s원)" % (
                    max_qty, "{:,}".format(max_qty * price)), False))
            else:
                checks.append(("매수 가능", False, "투자금 부족 (잔고: %s원, 주가: %s원)" % (
                    "{:,}".format(cash), "{:,}".format(price)), False))

        # 6. 거래 모드
        if self.kis.is_virtual:
            checks.append(("거래 모드", True, "⚠️ 모의투자", False))
        else:
            checks.append(("거래 모드", True, "🔴 실전", False))

        # 결과 종합: 필수 항목만 봇 시작 차단
        critical_ok = all(ok for _, ok, _, required in checks if required)
        has_warning = any(not ok for _, ok, _, required in checks if not required)
        lines = []
        for name, ok, detail, required in checks:
            if ok:
                mark = "✅"
            elif required:
                mark = "❌"
            else:
                mark = "⚠️"
            lines.append("%s %s: %s" % (mark, name, detail))

        status = "통과" if critical_ok and not has_warning else "경고있음" if critical_ok else "실패"
        report = "\n".join(lines)
        logger.info("[사전점검] %s\n%s", status, report)

        tg_report = "<b>🔍 사전점검 %s</b>\n\n%s" % (status, report)
        if not critical_ok:
            tg_report += "\n\n❌ 필수 항목 실패 — 봇을 시작할 수 없습니다."
        elif has_warning:
            tg_report += "\n\n⚠️ 경고 항목이 있지만 봇은 시작합니다."
        self.telegram.send(tg_report)

        return critical_ok

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

        # 실전 모드: 사전점검 필수
        if not self.kis.is_virtual and self.kis.is_authenticated:
            if not self.preflight_check():
                logger.error("사전점검 실패 — 봇을 시작할 수 없습니다.")
                return
        else:
            self.preflight_check()

        self.telegram.notify_start(scan_str, "주식 %s" % self.strategy.name, mode_str)

        while self.running:
            try:
                if self.is_market_open():
                    self.run_once()
                    self._heartbeat()
                else:
                    now = datetime.datetime.now()
                    # 장 마감 알림 (1회)
                    if not self._market_close_notified and now.hour == 15 and now.minute >= 21:
                        self._market_close_notified = True
                        summary = "📴 <b>장 마감</b>\n거래: %d건\nPnL: %+.0f원" % (
                            self._daily_trades, self.kill_switch.daily_pnl)
                        self.telegram.send(summary)
                        logger.info("[장 마감] 오늘 거래: %d건, PnL: %+.0f원",
                                    self._daily_trades, self.kill_switch.daily_pnl)
                        self._daily_trades = 0
                        self.scanner.clear_exclusions()
                    # 장외 시간 생존 확인 (3시간마다)
                    self._offhour_heartbeat()

                self._send_daily_report_if_needed()
            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                for _ in range(poll_sec):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("봇 종료")

    def _heartbeat(self):
        """매 시간 정각에 상태 로그 + 텔레그램"""
        now = time.time()
        if now - self._last_heartbeat < 3600:
            return
        self._last_heartbeat = now

        mode = self.get_trading_mode()
        hold_str = ""
        if self.position.is_holding:
            price = self._get_price()
            pnl = self._calc_pnl(price) if price > 0 else 0
            hold_str = "보유: %s %s (%+.1f%%)" % (self.stock_code, self._stock_name, pnl)
        else:
            hold_str = "보유: 없음 (스캔 중)" if self.auto_scan else "보유: 없음"

        # 코스피 + 심리 상태
        idx = self._index_cache
        idx_str = "코스피: %+.1f%%" % idx["change_pct"] if idx else "코스피: 조회중"

        sent = self.sentiment.analyze()
        sent_str = "심리: %s(%d점)" % (sent.sentiment, sent.score)
        if sent.vkospi > 0:
            sent_str += " VKOSPI:%.1f" % sent.vkospi

        status = "[정기보고] %s | %s | 거래:%d건 | PnL:%+.0f원 | %s | %s" % (
            mode, hold_str, self._daily_trades, self.kill_switch.daily_pnl, idx_str, sent_str)

        logger.info(status)
        self.telegram.send("<b>📋 주식봇 정기보고</b>\n%s\n%s\n거래: %d건\nPnL: %+.0f원\n%s\n%s" % (
            mode, hold_str, self._daily_trades, self.kill_switch.daily_pnl, idx_str, sent_str))

    def _offhour_heartbeat(self):
        """장외 시간 생존 확인 (3시간마다)"""
        now = time.time()
        if now - self._last_offhour_heartbeat < 10800:  # 3h
            return
        self._last_offhour_heartbeat = now
        hour = datetime.datetime.now().strftime("%H:%M")
        self.telegram.send("💤 주식봇 대기 중 (%s)" % hour)
        logger.info("[대기 중] 장외 시간 — 봇 정상 작동")

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
