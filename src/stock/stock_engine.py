"""한국 주식 자동매매 엔진 v6 (스윙 전환)

v5 → v6 변경점 (스캘핑 → 스윙 전환):
  - 전일 스캐너 관심종목 → 다음날 눌림목 매수 (2-5일 보유)
  - 손절 -3% / 익절 +5% / 트레일링 -2% / 5거래일 보유제한
  - 장 마감 전 관심종목 스캔 저장 (watchlist.json)
  - 눌림목 진입: 전일종가-3% 또는 5일선 지지
  - 분봉 스캘핑 매도 → 일봉 기준 손익절로 전환

v4 유지:
  - BaseTradingEngine 상속, ATR 기반 분할익절
  - 시초가 갭/VI/수급/코스피 필터
  - 장마감 시간 관리, 멀티 포지션 (최대 3종목)
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
from src.strategies.scalping import ScalpingStrategy
from src.utils.telegram_bot import TelegramNotifier
from src.utils.safety import KillSwitch, TradeLogger
from src.utils.daily_report import DailyReport
from src.trader.base_engine import BaseTradingEngine
from .kis_client import KISClient
from .scanner import StockScanner
from .watchlist import Watchlist, WatchItem
from .investor_flow import InvestorFlow
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
CLOSING_MODE = datetime.time(15, 10)

STOCK_FEE = 0.0010  # 국내주식 수수료+세금 (수수료0.015%×2 + 거래세0.05~0.15% ≒ 편도0.1%)


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
    """한국 주식 자동매매 v6 스윙 (BaseTradingEngine 상속)"""

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
        stop_loss_pct: float = 3.0,
        take_profit_pct: float = 5.0,
        trailing_pct: float = 2.0,
        atr_stop_multiplier: float = 2.5,
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
        self._scalping = ScalpingStrategy()
        self.sentiment = MarketSentiment(self.kis)
        self.watchlist = Watchlist()
        self.investor_flow = InvestorFlow()
        self.position = StockPosition(code=stock_code)  # 호환용 (고정종목 모드)
        self.positions: Dict[str, StockPosition] = {}  # 멀티 종목 포지션
        self.max_positions = 3  # 최대 동시 보유 종목 수
        self._watchlist_saved_today: str = ""  # 오늘 관심종목 저장 여부
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
        self._market_filter_cache: dict = {}  # 시장 국면 필터 캐시
        self._market_filter_time: float = 0
        self._last_report_date = ""
        self._today_open_price: Dict[str, float] = {}
        self._last_block_reason: str = ""
        self._last_block_time: float = 0
        self._last_heartbeat: float = 0
        self._last_offhour_heartbeat: float = 0
        self._market_open_notified: str = ""  # 장 시작 알림 날짜
        self._market_close_notified: bool = False
        # 재진입 차단 복원 범위(분): 기본 0(복원 비활성, 스캔 우선)
        self.sell_exclusion_minutes = 0

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

        # 1. 코스피 급락 체크
        if self._index_cache:
            idx_change = self._index_cache.get("change_pct", 0)
            if idx_change <= -3.0:
                return False, "코스피 급락 (%.1f%%)" % idx_change

        # 2. VKOSPI 공포지수 체크 (25 이상 = 패닉)
        sent = self.sentiment.analyze()
        if sent.vkospi >= 25:
            return False, "VKOSPI 공포 (%.1f)" % sent.vkospi

        # 3. 코스피 20일선 체크 (30분마다 갱신)
        if now - self._market_filter_time > 1800:
            self._market_filter_cache = self._check_index_ma20()
            self._market_filter_time = now
        if self._market_filter_cache.get("below_ma20"):
            return False, "코스피 20일선 하회 (지수:%.0f < 20MA:%.0f)" % (
                self._market_filter_cache.get("price", 0),
                self._market_filter_cache.get("ma20", 0))

        return True, ""

    def _check_index_ma20(self) -> dict:
        """KODEX 200(069500) 일봉으로 코스피 20일선 상태 판단"""
        try:
            df = self.kis.get_ohlcv("069500", period="D", count=25)
            if df is None or len(df) < 20:
                return {}
            ma20 = float(df["close"].iloc[-20:].mean())
            cur_price = float(df["close"].iloc[-1])
            below = cur_price < ma20
            if below:
                logger.info("[MarketFilter] 코스피 20일선 하회: KODEX200 %.0f < MA20 %.0f", cur_price, ma20)
            return {"below_ma20": below, "price": cur_price, "ma20": ma20}
        except Exception as e:
            logger.warning("[MarketFilter] KODEX200 20일선 조회 실패: %s", e)
            return {}

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

        result = self.kis.buy(self.stock_code, qty, price=price)
        if result and result.get("success"):
            new_pos = StockPosition(
                code=self.stock_code, name=self._stock_name,
                avg_price=price, quantity=qty, highest_price=price,
                entry_atr=current_atr, entry_time=time.time(),
            )
            self.position = new_pos
            self.positions[self.stock_code] = new_pos
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

            try:
                self.telegram.send(
                    "<b>🟢 매수</b>\n"
                    "종목: <code>%s %s</code>\n"
                    "가격: %s원 × %d주 = %s원\n\n%s"
                    % (self.stock_code, self.telegram.escape(self._stock_name),
                       "{:,}".format(price), qty, "{:,}".format(qty * price),
                       outlook)
                )
            except Exception as e:
                logger.warning("[매수] 텔레그램 전송 실패: %s", e)
            self.trade_logger.log(
                bot="stock_trader", side="BUY", symbol=self.stock_code,
                exchange="KIS", price=price, quantity=qty, amount=qty * price,
                fee=qty * price * self.fee_rate, reason=reason)
            return True
        else:
            error = result.get("error", "") if result else "알 수 없음"
            logger.error("[매수 실패] %s: %s", self.stock_code, error)
            try:
                self.telegram.notify_error("매수 실패: %s\n%s" % (error, self.stock_code))
            except Exception:
                pass
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

        result = self.kis.sell(self.stock_code, qty, price=price)
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
                "수익: %s원 (%+.2f%%)\n"
                "보유: %s\n"
            ) % (
                self.telegram.escape(reason),
                "{:,}".format(self.position.avg_price),
                "{:,}".format(price),
                "{:+,.0f}".format(pnl_amount), pnl_pct,
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
                # 멀티 포지션 동기화
                if self.stock_code in self.positions:
                    self.positions[self.stock_code].quantity = self.position.quantity
            else:
                self.kill_switch.record_trade(pnl_amount)
                # 매도 종목 재매수 방지 (옵션): sell_exclusion_minutes>0일 때만 유지
                if self.sell_exclusion_minutes > 0:
                    self.scanner.exclude(self.stock_code)
                # v4: 공통 승률 추적 + 쿨다운 로직
                self.record_trade_result(pnl_pct)
                if self._consecutive_losses >= self._max_consecutive_losses:
                    self.telegram.send(
                        "<b>⏸ 쿨다운</b>\n%d연속 손실 → %d분 대기"
                        % (self._consecutive_losses, self._cooldown_minutes))
                self.position = StockPosition(code=self.stock_code)
                # 멀티 포지션에서 제거
                self.positions.pop(self.stock_code, None)
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

        # 코스피 급락 (최초 1회만 알림, 회복 후 재발생 시 다시 알림)
        mkt_ok, mkt_reason = self._check_market_conditions()
        if not mkt_ok:
            if not self._last_block_reason.startswith("코스피"):
                try:
                    self.telegram.send("<b>🚫 매수 차단</b>\n사유: %s" % mkt_reason)
                except Exception:
                    pass
            self._last_block_reason = mkt_reason
            self._last_block_time = now
            return False, mkt_reason
        else:
            # 코스피 회복 시 차단 사유 초기화
            if self._last_block_reason.startswith("코스피"):
                self._last_block_reason = ""

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

        # ── 자동 스캔: 멀티 종목 관리 ──
        if self.auto_scan:
            self._run_multi_positions(mode)
            return

        # ── 고정 종목 모드 (기존 단일 종목 로직) ──
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
            self._manage_single_position(mode, df, price)
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

    def _check_entry_timing(self, code: str, name: str, current_price: int) -> tuple:
        """분봉 기반 진입 타이밍 검증.

        세 가지 중 하나라도 통과하면 매수 허용:
        1. 눌림목 진입: 급등 후 조정 중 지지선 근처에서 반등
        2. 장 초반 갭업 돌파: 09:05~09:30 전일 고가 돌파
        3. N분봉 고가 돌파: 최근 고가를 갱신하며 상승 중
        """
        mdf = self.kis.get_minute_ohlcv(code)
        if mdf is None or len(mdf) < 5:
            return False, "분봉데이터부족(매수불가)"

        now_t = datetime.datetime.now().time()
        highs = mdf["high"].values
        lows = mdf["low"].values
        closes = mdf["close"].values
        latest_close = int(closes[-1])

        # ── 1. 눌림목 진입 ──
        # 분봉 고점 대비 2~5% 조정 후 반등 시작
        intraday_high = int(max(highs))
        intraday_low = int(min(lows[-5:]))  # 최근 5봉 저가
        if intraday_high > 0:
            drop_from_high = (intraday_high - intraday_low) / intraday_high * 100
            recover_from_low = (latest_close - intraday_low) / intraday_low * 100 if intraday_low > 0 else 0

            # 고점 대비 2~8% 빠졌다가, 저점 대비 1%+ 반등 중
            if 2.0 <= drop_from_high <= 8.0 and recover_from_low >= 1.0:
                # 최근 3봉이 상승 추세인지 확인
                if len(closes) >= 3 and closes[-1] > closes[-2] >= closes[-3]:
                    return True, "눌림목반등(고점대비-%.1f%%→+%.1f%%)" % (drop_from_high, recover_from_low)

        # ── 2. 장 초반 갭업 돌파 (09:05~09:30) ──
        if datetime.time(9, 5) <= now_t <= datetime.time(9, 30):
            # 전일 고가 = 일봉 데이터에서 가져오기
            daily = self.kis.get_ohlcv(code, period="D", count=3)
            if daily is not None and len(daily) >= 2:
                prev_high = int(daily["high"].iloc[-2])
                if latest_close > prev_high:
                    gap_pct = (latest_close - prev_high) / prev_high * 100
                    if gap_pct <= 10:  # 너무 큰 갭은 위험
                        return True, "장초반갭업돌파(전일고가%d→현재%d)" % (prev_high, latest_close)

        # ── 3. N분봉 고가 돌파 ──
        # 최근 10봉 고가를 현재가가 돌파하면서 거래량도 증가
        if len(mdf) >= 10:
            recent_high = int(max(highs[-10:-1]))  # 직전 9봉 고가
            recent_avg_vol = mdf["volume"].iloc[-10:-1].mean()
            latest_vol = int(mdf["volume"].iloc[-1])

            if latest_close > recent_high and latest_vol > recent_avg_vol * 1.2:
                return True, "분봉돌파(직전고가%d<현재%d,거래량%.1fx)" % (
                    recent_high, latest_close,
                    latest_vol / recent_avg_vol if recent_avg_vol > 0 else 0)

        # 모두 미충족
        drop_info = ""
        if intraday_high > 0:
            pos_pct = (latest_close - intraday_low) / (intraday_high - intraday_low) * 100 if intraday_high != intraday_low else 100
            drop_info = "고점대비%.1f%%위치" % (100 - pos_pct) if pos_pct < 100 else "고점"
        return False, "꼭대기매수방지(%s)" % (drop_info or "분석불가")

    def _manage_single_position(self, mode: str, df, price: int):
        """단일 포지션 손절/익절 관리 (고정 종목 모드용)"""
        self.position.update_highest(price)
        self._last_df = df
        pnl_pct = self._calc_pnl(price)
        label = "%s %s" % (self.stock_code, self._stock_name)

        if self._check_stop_loss(price):
            detail = self.get_stop_loss_detail(
                self.position.avg_price, price,
                self.position.entry_atr, self.position.partial_stage)
            self.telegram.notify_stop_loss(label, price, abs(pnl_pct))
            self._sell(detail)
            self._last_stop_loss_time = time.time()
            self._last_sell_time = time.time()
            return

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

        if self._check_trailing(price):
            detail = self.get_trailing_detail(
                self.position.avg_price, price,
                self.position.highest_price, self.position.entry_atr)
            self.telegram.notify_take_profit(label, price, pnl_pct)
            self._sell(detail)
            self._last_sell_time = time.time()
            return

        if mode == "closing":
            if pnl_pct > 0:
                self._sell("장마감 전 익절 (%+.1f%%)" % pnl_pct)
            elif pnl_pct < -1.0:
                self._sell("장마감 전 손절 (%+.1f%%)" % pnl_pct)

    def _run_multi_positions(self, mode: str):
        """멀티 종목 관리: 보유 종목 손절/익절 + 빈 슬롯이면 스캔"""
        # ── 1. 잔고 동기화 ──
        balance = self.kis.get_balance() if self.kis.is_authenticated else None
        if balance:
            live_codes = set()
            for h in balance["holdings"]:
                code = h["code"]
                live_codes.add(code)
                if code in self.positions:
                    self.positions[code].quantity = h["quantity"]
                    self.positions[code].avg_price = h["avg_price"]
                elif h["quantity"] > 0:
                    # 봇 외부에서 매수한 종목이면 추적 시작
                    self.positions[code] = StockPosition(
                        code=code, name=h.get("name", ""),
                        avg_price=h["avg_price"], quantity=h["quantity"],
                        highest_price=h["avg_price"], entry_time=time.time(),
                    )
            # 잔고에 없는 포지션 제거 (이미 매도 완료)
            for code in list(self.positions):
                if code not in live_codes:
                    del self.positions[code]

        # ── 2. 보유 종목 손절/익절 관리 ──
        for code, pos in list(self.positions.items()):
            if pos.quantity <= 0:
                continue

            info = self.kis.get_current_price(code)
            if not info:
                continue
            cur_price = info["price"]
            pos.update_highest(cur_price)

            pnl_pct = self.calc_pnl(pos.avg_price, cur_price)
            label = "%s %s" % (code, pos.name)

            # 임시로 self.stock_code/position 설정 (_sell이 사용)
            saved_code = self.stock_code
            saved_name = self._stock_name
            saved_pos = self.position
            self.stock_code = code
            self._stock_name = pos.name
            self.position = pos

            sold = False

            # 손절
            if self.check_stop_loss(pos.avg_price, cur_price, pos.entry_atr, pos.partial_stage):
                detail = self.get_stop_loss_detail(pos.avg_price, cur_price, pos.entry_atr, pos.partial_stage)
                try:
                    self.telegram.notify_stop_loss(label, cur_price, abs(pnl_pct))
                except Exception:
                    pass
                self._sell(detail)
                sold = True

            # 분할익절
            elif pos.partial_stage == 0:
                tp1, _ = self.get_partial_triggers(pos.avg_price, pos.entry_atr)
                if pnl_pct >= tp1:
                    self._sell("1차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (pnl_pct, tp1), partial=True)
                    pos.partial_stage = 1

            elif pos.partial_stage == 1:
                _, tp2 = self.get_partial_triggers(pos.avg_price, pos.entry_atr)
                if pnl_pct >= tp2:
                    self._sell("2차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (pnl_pct, tp2), partial=True)
                    pos.partial_stage = 2

            # 트레일링
            elif self.check_trailing_stop(pos.avg_price, cur_price, pos.highest_price, pos.entry_atr, pos.partial_stage):
                detail = self.get_trailing_detail(pos.avg_price, cur_price, pos.highest_price, pos.entry_atr)
                try:
                    self.telegram.notify_take_profit(label, cur_price, pnl_pct)
                except Exception:
                    pass
                self._sell(detail)
                sold = True

            # 스윙 보유기간 체크 (5거래일 초과 시 정리)
            if not sold:
                hold_days = (time.time() - pos.entry_time) / 86400
                if hold_days >= 5 and mode == "closing":
                    if pnl_pct > 0:
                        self._sell("보유기한 5일 익절 (%+.1f%%)" % pnl_pct)
                    else:
                        self._sell("보유기한 5일 정리 (%+.1f%%)" % pnl_pct)
                    sold = True

            # 복원
            self.stock_code = saved_code
            self._stock_name = saved_name
            self.position = saved_pos

        # ── 3. 장 마감 전: 내일 관심종목 스캔 저장 ──
        today = datetime.date.today().isoformat()
        if mode == "closing" and self._watchlist_saved_today != today:
            self._save_watchlist_for_tomorrow(today)
            self._watchlist_saved_today = today

        # ── 4. 빈 슬롯 있으면 관심종목 눌림목 매수 ──
        holding_count = sum(1 for p in self.positions.values() if p.quantity > 0)
        if holding_count >= self.max_positions:
            return

        if mode == "opening_wait":
            return

        now = time.time()
        if self.is_in_cooldown():
            return

        self._run_swing_buy(now, today)

    def _save_watchlist_for_tomorrow(self, today: str):
        """장 마감 전: 스캐너로 내일 관심종목 저장"""
        # 하락장이면 스캔 자체 스킵 (안 좋은 종목 담지 않음)
        mkt_ok, mkt_reason = self._check_market_conditions()
        if not mkt_ok:
            logger.info("[스윙] 하락장 (%s) → 관심종목 스캔 스킵", mkt_reason)
            return

        logger.info("[스윙] 관심종목 스캔 시작 (내일 매수 후보)")
        candidates = self.scanner.get_candidates(limit=10)
        if not candidates:
            logger.info("[스윙] 스캐너 후보 없음")
            return

        watch_items = []
        for best in candidates:
            # 급등주 제외 (너무 많이 오른 건 조정폭도 큼)
            if best.change_pct >= 15 or best.change_pct < 2:
                continue

            # 최소 거래대금 100억 이상 (소형 잡주 제외)
            if best.trade_value < 10_000_000_000:
                logger.debug("[스윙] %s 거래대금 부족 (%s억) → 제외",
                             best.name, "{:,.0f}".format(best.trade_value / 100_000_000))
                continue

            # 일봉 데이터로 이평선 계산
            scan_df = self.kis.get_ohlcv(best.code, period="D", count=30)
            ma5, ma20 = 0.0, 0.0
            if scan_df is not None and len(scan_df) >= 20:
                scan_df = self.indicators.add_all(scan_df)
                if "ma_short" in scan_df.columns:
                    v = scan_df["ma_short"].iloc[-1]
                    if pd.notna(v):
                        ma5 = float(v)
                if "ma_long" in scan_df.columns:
                    v = scan_df["ma_long"].iloc[-1]
                    if pd.notna(v):
                        ma20 = float(v)

            # 눌림목 목표가: 전일 종가 -3% 또는 5일선 중 높은 가격
            pullback = max(int(best.price * 0.97), int(ma5)) if ma5 > 0 else int(best.price * 0.97)

            # 외국인/기관 수급 체크 (pykrx)
            foreign_flow, inst_flow = 0, 0
            flow_bonus = 0
            flow = self.investor_flow.get_flow(best.code, days=5)
            if flow:
                foreign_flow = flow["foreign_consecutive_buy"]
                if flow["foreign_consecutive_sell"] > 0:
                    foreign_flow = -flow["foreign_consecutive_sell"]
                inst_flow = flow["inst_consecutive_buy"]
                # 외국인 3일 연속 순매도 → 제외
                if flow["foreign_consecutive_sell"] >= 3:
                    logger.info("[스윙] %s 외국인 %d일 연속 순매도 → 제외",
                                best.name, flow["foreign_consecutive_sell"])
                    continue
                # 수급 가산점
                if flow["both_buying"]:
                    flow_bonus += 20  # 외인+기관 동반 순매수
                if flow["foreign_consecutive_buy"] >= 3:
                    flow_bonus += 15  # 외인 3일 연속 순매수
                if flow["inst_consecutive_buy"] >= 1:
                    flow_bonus += 10  # 기관 순매수

            item = WatchItem(
                code=best.code,
                name=best.name,
                close=best.price,
                change_pct=best.change_pct,
                score=best.score + flow_bonus,
                reasons=best.reasons[:5] + (["수급+%d" % flow_bonus] if flow_bonus > 0 else []),
                trade_value=best.trade_value,
                ma5=ma5,
                ma20=ma20,
                pullback_target=pullback,
                foreign_flow=foreign_flow,
                inst_flow=inst_flow,
            )
            watch_items.append(item)
            flow_str = " | 외인:%+d일 기관:%+d일" % (foreign_flow, inst_flow) if flow else ""
            logger.info("[스윙] 관심종목: %s %s | 종가:%s원 | 5MA:%s원 | 목표매수:%s원 | 점수:%.0f%s",
                        best.code, best.name,
                        "{:,}".format(best.price),
                        "{:,}".format(int(ma5)),
                        "{:,}".format(pullback),
                        best.score, flow_str)

        if watch_items:
            self.watchlist.update_candidates(watch_items, today)
            try:
                def _flow_tag(w):
                    if w.foreign_flow > 0:
                        return " 외인%+d일" % w.foreign_flow
                    elif w.foreign_flow < 0:
                        return " 외인%+d일" % w.foreign_flow
                    return ""
                summary = "\n".join(
                    "• %s %s (%.0f점) 목표:%s원%s" % (
                        w.code, w.name, w.score,
                        "{:,}".format(int(w.pullback_target)), _flow_tag(w))
                    for w in watch_items[:5])
                self.telegram.send(
                    "<b>📋 내일 관심종목</b>\n%s\n\n총 %d종목 저장"
                    % (summary, len(watch_items)))
            except Exception:
                pass
        else:
            logger.info("[스윙] 관심종목 조건 충족 종목 없음")

    def _run_swing_buy(self, now: float, today: str):
        """관심종목 눌림목 매수: 전일 종가 대비 조정 시 진입"""
        # 매수 전 필터
        ok, reason = self._pre_buy_checks(now, None, 0)
        if not ok:
            logger.debug("[스윙매수] 매수 필터 차단: %s", reason)
            return

        active = self.watchlist.get_active(today)
        if not active:
            logger.debug("[스윙매수] 활성 관심종목 없음")
            return

        bought_count = 0
        for item in active:
            # 이미 보유 중이면 스킵
            if item.code in self.positions and self.positions[item.code].quantity > 0:
                continue

            # 현재가 조회
            info = self.kis.get_current_price(item.code)
            if not info:
                continue
            cur_price = info["price"]
            cur_change = info.get("change_pct", 0)

            # 오늘 급락 중이면 스킵 (추가 하락 위험)
            if cur_change <= -7:
                logger.info("[스윙매수] %s 급락 중 (%+.1f%%) → 스킵", item.name, cur_change)
                continue

            # ── 눌림목 조건 체크 ──
            # 조건1: 현재가가 목표 매수가 이하 (전일 종가 -3% 또는 5일선)
            pullback_ok = cur_price <= item.pullback_target

            # 조건2: 일봉 기준 5일선 지지 확인
            scan_df = self.kis.get_ohlcv(item.code, period="D", count=10)
            ma5_support = False
            atr = 0.0
            if scan_df is not None and len(scan_df) >= 5:
                scan_df = self.indicators.add_all(scan_df)
                if "ma_short" in scan_df.columns:
                    ma5_now = scan_df["ma_short"].iloc[-1]
                    if pd.notna(ma5_now) and cur_price <= float(ma5_now) * 1.01:
                        ma5_support = True
                if "atr" in scan_df.columns:
                    atr_val = scan_df["atr"].iloc[-1]
                    if pd.notna(atr_val):
                        atr = float(atr_val)

            if not pullback_ok and not ma5_support:
                logger.debug("[스윙매수] %s 눌림목 미도달 (현재:%s, 목표:%s, 5MA지지:%s)",
                             item.name, "{:,}".format(cur_price),
                             "{:,}".format(int(item.pullback_target)),
                             ma5_support)
                continue

            # ── 거래량 체크: 음봉+거래량 급증=투매, 양봉+거래량 급증=매수세 ──
            if scan_df is not None and len(scan_df) >= 3:
                today_vol = float(scan_df["volume"].iloc[-1]) if "volume" in scan_df.columns else 0
                prev_vol = float(scan_df["volume"].iloc[-2]) if "volume" in scan_df.columns else 1
                today_close = float(scan_df["close"].iloc[-1])
                today_open = float(scan_df["open"].iloc[-1])
                is_bearish = today_close < today_open
                if prev_vol > 0 and today_vol > prev_vol * 1.5 and is_bearish:
                    logger.info("[스윙매수] %s 음봉+거래량 급증 (%.0fx) → 투매 가능 → 스킵",
                                item.name, today_vol / prev_vol)
                    continue

            # ── 체결강도 체크 ──
            vp = self.kis.get_volume_power(item.code)
            if vp < 70:
                logger.info("[스윙매수] %s 체결강도 약세 (%.0f%%) → 스킵", item.name, vp)
                continue

            # ── 분봉 반등 확인 (양봉 2개 + 거래량 + VWAP 탈환) ──
            mdf = self.kis.get_minute_ohlcv(item.code)
            vwap_ok = False
            if mdf is not None and len(mdf) >= 3:
                c1 = mdf.iloc[-2]
                c2 = mdf.iloc[-1]
                bull1 = float(c1["close"]) > float(c1["open"])
                bull2 = float(c2["close"]) > float(c2["open"])
                vol_ok = float(c2["volume"]) > float(c1["volume"]) * 0.8
                if not (bull1 and bull2 and vol_ok):
                    logger.debug("[스윙매수] %s 반등 미확인 (양봉:%s/%s 거래량:%s) → 대기",
                                 item.name, bull1, bull2, vol_ok)
                    continue

                # VWAP 계산: 현재가가 VWAP 위에 있으면 매수세 우위
                tp = (mdf["high"].astype(float) + mdf["low"].astype(float) + mdf["close"].astype(float)) / 3
                vol = mdf["volume"].astype(float)
                cum_vol = vol.cumsum()
                if cum_vol.iloc[-1] > 0:
                    vwap = float((tp * vol).cumsum().iloc[-1] / cum_vol.iloc[-1])
                    latest = float(c2["close"])
                    vwap_ok = latest >= vwap
                    if not vwap_ok:
                        logger.debug("[스윙매수] %s VWAP 하회 (현재:%s < VWAP:%s) → 대기",
                                     item.name, "{:,}".format(int(latest)), "{:,}".format(int(vwap)))
                        continue

            # ── 매수 실행 ──
            reason_parts = []
            if pullback_ok:
                reason_parts.append("눌림목(목표%s원도달)" % "{:,}".format(int(item.pullback_target)))
            if ma5_support:
                reason_parts.append("5MA지지")
            if vwap_ok:
                reason_parts.append("VWAP탈환")
            reason_parts.append("관심종목(%.0f점)" % item.score)
            buy_reason = "스윙매수: %s | 원래사유: %s" % (
                " + ".join(reason_parts), ", ".join(item.reasons[:3]))

            old_code = self.stock_code
            old_name = self._stock_name
            self.stock_code = item.code
            self._stock_name = item.name
            self._supply_cache = None
            self._supply_cache_time = 0

            logger.info("[스윙매수] %s %s 매수 시도 (현재:%s원, 관심종목종가:%s원, %+.1f%%)",
                        item.code, item.name,
                        "{:,}".format(cur_price),
                        "{:,}".format(item.close),
                        (cur_price - item.close) / item.close * 100)

            if self._buy(buy_reason, current_atr=atr, confidence=0.6):
                try:
                    self.telegram.send(
                        "<b>📈 스윙 매수</b>\n"
                        "종목: %s %s\n"
                        "매수가: %s원 (관심종목 종가: %s원)\n"
                        "목표: +5%% | 손절: -3%%\n"
                        "사유: %s"
                        % (item.code, item.name,
                           "{:,}".format(cur_price),
                           "{:,}".format(item.close),
                           buy_reason))
                except Exception:
                    pass
                bought_count += 1
                holding_count = sum(1 for p in self.positions.values() if p.quantity > 0)
                logger.info("[스윙매수] %s 매수 완료, 보유 %d/%d",
                            item.name, holding_count, self.max_positions)
                if holding_count >= self.max_positions:
                    break
            else:
                self.stock_code = old_code
                self._stock_name = old_name

        if bought_count > 0:
            logger.info("[스윙매수] %d종목 매수 완료", bought_count)

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

    def _restore_today_exclusions(self):
        """재시작 시 당일 매도 종목 일부를 스캐너 제외 목록에 복원.

        - 최근 `sell_exclusion_minutes`분 내 매도 종목만 제외
        - 현재 보유 중인 종목은 제외 대상에서 제거
        """
        import csv
        import os
        csv_path = "logs/trades.csv"
        if not os.path.exists(csv_path):
            return
        today = datetime.date.today().isoformat()
        now = datetime.datetime.now()
        cutoff_min = max(0, int(self.sell_exclusion_minutes))
        if cutoff_min == 0:
            logger.info("[복원] 매도 제외 복원 비활성 (sell_exclusion_minutes=0)")
            return

        # 현재 보유 종목은 제외 복원 대상에서 제외 (부분매도/수동체결 호환)
        holding_codes = set()
        balance = self.kis.get_balance() if self.kis.is_authenticated else None
        if balance:
            holding_codes = {h.get("code", "") for h in balance.get("holdings", []) if h.get("quantity", 0) > 0}

        restored = []
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dt = row.get("datetime", "")
                    if not dt.startswith(today) or row.get("bot") != "stock_trader":
                        continue

                    # 최근 N분 내 매도만 복원. 파싱 실패 시 보수적으로 스킵.
                    try:
                        trade_dt = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
                    age_min = (now - trade_dt).total_seconds() / 60.0
                    if cutoff_min > 0 and age_min > cutoff_min:
                        continue

                    code = row.get("symbol", "")
                    if code and row.get("side") == "SELL" and code not in holding_codes:
                        self.scanner.exclude(code)
                        restored.append(code)
            excluded = self.scanner._excluded
            if excluded:
                logger.info("[복원] 최근 %d분 내 매도 제외(%d): %s",
                            cutoff_min, len(excluded), ", ".join(sorted(excluded)))
            elif restored:
                logger.info("[복원] 최근 매도 제외 복원됨 (%d건)", len(restored))
            else:
                logger.info("[복원] 최근 %d분 내 제외 복원 대상 없음", cutoff_min)
        except Exception as e:
            logger.debug("[복원] 제외 목록 로드 실패: %s", e)

    def start(self, poll_sec: int = 10):
        self.running = True

        # 재시작 시 당일 매매 종목 exclusion 복원
        self._restore_today_exclusions()

        def _stop(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        mode_str = "모의투자" if self.kis.is_virtual else "실전"
        if not self.kis.is_authenticated:
            mode_str = "시뮬레이션"
        scan_str = "자동 스캔" if self.auto_scan else "고정: %s" % self.stock_code

        logger.info("=" * 60)
        strat_name = "Swing(스윙2-5일)" if self.auto_scan else self.strategy.name
        logger.info("  주식 자동매매 봇 v6 시작 (스윙 전환)")
        logger.info("  종목: %s | 전략: %s | 모드: %s", scan_str, strat_name, mode_str)
        logger.info("  투자: %.0f%% (최대 %s원) | 수수료: %.3f%%",
                     self.invest_ratio * 100, "{:,}".format(self.max_invest_krw), self.fee_rate * 100)
        logger.info("  손절: -%.1f%% (ATR×%.1f) | 익절: +%.1f%%→분할→트레일링%.1f%%",
                     self.stop_loss_pct, self.atr_stop_multiplier,
                     self.take_profit_pct, self.trailing_pct)
        logger.info("  보유: 최대 5거래일 | 보호: 3연속손실→쿨다운 | 일일-3%%→Kill Switch")
        logger.info("  진입: 전일 관심종목 눌림목 매수 (종가-3%% 또는 5MA지지)")
        logger.info("  시장필터: VKOSPI≥25 차단 + 코스피20일선 하회 차단")
        logger.info("  수급필터: 외국인 3일연속 순매도 종목 제외 (pykrx)")
        logger.info("  장: 09:05관망→10:00골든→15:10관심종목스캔")
        logger.info("=" * 60)

        # 실전 모드: 사전점검 필수
        if not self.kis.is_virtual and self.kis.is_authenticated:
            if not self.preflight_check():
                logger.error("사전점검 실패 — 봇을 시작할 수 없습니다.")
                return
        else:
            self.preflight_check()

        self.telegram.notify_start(scan_str, "주식 %s" % self.strategy.name, mode_str)

        # 시작 시 관심종목 없으면 즉시 스캔 (장 마감 후 시작 대비)
        if self.auto_scan and self.kis.is_authenticated:
            today = datetime.date.today().isoformat()
            active = self.watchlist.get_active(today)
            if not active and self._watchlist_saved_today != today:
                logger.info("[시작] 관심종목 없음 → 즉시 스캔")
                try:
                    self._save_watchlist_for_tomorrow(today)
                    self._watchlist_saved_today = today
                except Exception as e:
                    logger.warning("[시작] 관심종목 스캔 실패: %s", e)

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
        active_positions = {c: p for c, p in self.positions.items() if p.quantity > 0}
        if active_positions:
            parts = []
            for code, pos in active_positions.items():
                info = self.kis.get_current_price(code)
                pnl = self.calc_pnl(pos.avg_price, info["price"]) if info else 0
                parts.append("%s(%+.1f%%)" % (pos.name or code, pnl))
            hold_str = "보유%d/%d: %s" % (len(active_positions), self.max_positions, " / ".join(parts))
        elif self.position.is_holding:
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
        if self.daily_report.already_sent(yesterday):
            logger.debug("[일일리포트] %s 이미 전송됨 (다른 봇)", yesterday)
            self._last_report_date = today
            return
        report = self.daily_report.generate(yesterday)
        self.telegram.send(report)
        logger.info("[일일리포트] %s 전송 완료", yesterday)
        self._last_report_date = today
