"""단일 거래소(업비트) 기술적 분석 자동매매 엔진"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import pyupbit

from src.indicators.technical import TechnicalIndicators
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.trader.base_engine import BaseTradingEngine
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.combined import CombinedStrategy
from src.strategies.adaptive import AdaptiveStrategy
from src.strategies.fear_greed import FearGreedStrategy
from src.utils.telegram_bot import TelegramNotifier
from src.utils.safety import KillSwitch, TradeLogger, APIGuard
from src.utils.daily_report import DailyReport
from src.intelligence.correlation import CoinCorrelation

logger = logging.getLogger(__name__)

STRATEGY_MAP: Dict[str, type] = {
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "combined": CombinedStrategy,
    "adaptive": AdaptiveStrategy,
    "feargreed": FearGreedStrategy,
}


@dataclass
class Position:
    ticker: str
    avg_price: float = 0.0
    volume: float = 0.0
    entry_time: float = 0.0
    highest_price: float = 0.0
    entry_atr: float = 0.0
    partial_sold: bool = False   # 호환용 (1단계 완료 여부)
    partial_stage: int = 0       # 다단계 분할매도 단계 (0=미매도, 1=1차, 2=2차)
    breakeven_stop: bool = False # v4: 분할익절 후 손익분기 스톱 활성

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


class TraderEngine(BaseTradingEngine):
    """
    업비트 단일 거래소 자동매매 (BaseTradingEngine 상속).

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
        # 공통 매매 로직 초기화
        super().__init__(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_pct=trailing_pct,
            atr_stop_multiplier=atr_stop_multiplier,
            fee_rate=0.0005,
        )

        self.ticker = ticker
        self.interval = interval
        self.invest_ratio = invest_ratio
        self.max_invest_krw = max_invest_krw
        self.partial_exit_pct = 40.0
        self.partial_trigger_pct = None
        self.candle_count = candle_count

        self._upbit: Optional[pyupbit.Upbit] = None
        if access_key and secret_key:
            self._upbit = pyupbit.Upbit(access_key, secret_key)

        self.indicators = TechnicalIndicators()
        self.strategy = self._make_strategy(strategy_name)
        self.position = Position(ticker=ticker)
        self.trade_logs: List[TradeLog] = []
        self.running = False
        self._stop_event = threading.Event()
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self.kill_switch = KillSwitch(max_daily_loss_pct=3.0)
        self.trade_logger = TradeLogger()
        self.api_guard = APIGuard(calls_per_sec=4)
        self.daily_report = DailyReport()
        self._last_report_date: str = ""
        self._last_indicators: Dict = {}
        self._last_heartbeat: float = 0
        self.correlation = CoinCorrelation()

        # 코인 고유 설정
        self._htf_update_interval = 300
        self._htf_last_update: float = 0
        self._last_alert_reason: str = ""
        self._last_alert_time: float = 0

        # v5: 세션 기반 거래 필터 (UTC 시간)
        # 연구: UTC 16-17시(KST 01-02시) 거래량/변동성 피크
        # 고유동성 세션에서만 신규 진입, 저유동성 세션에서는 보수적
        self._active_sessions_utc = [
            (7, 11),   # 아시아 세션 (KST 16-20)
            (13, 18),  # 유럽-미국 교차 (KST 22-03) — 피크 포함
        ]
        self._session_conf_boost = 0.05  # 활성 세션 신뢰도 보너스
        self._session_conf_penalty = 0.1  # 비활성 세션 신뢰도 감점

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

    def _is_active_session(self) -> bool:
        """v5: 현재 UTC 시간이 고유동성 세션인지 확인"""
        hour = dt.datetime.utcnow().hour
        for start, end in self._active_sessions_utc:
            if start <= hour < end:
                return True
        return False

    def _buy(self, reason: str, current_atr: float = 0.0, confidence: float = 0.5) -> bool:
        krw = self._get_krw_balance()
        # v4: 신뢰도 × 승률 기반 투자금 조절 (공통 로직)
        size_mult = self.get_confidence_multiplier(confidence)
        amount = min(krw * self.invest_ratio * size_mult, self.max_invest_krw)
        if amount < 5000:
            logger.info("[매수 불가] 잔고 부족: %.0f원 (투자금: %.0f원 < 최소 5,000원)", krw, amount)
            self._alert_once(
                "잔고부족",
                "<b>⚠️ 매수 불가</b>\n"
                "사유: 잔고 부족\n"
                "KRW 잔고: %s원\n"
                "투자금: %s × %.0f%% = %s원 (최소 5,000원)\n"
                "신호: %s"
                % ("{:,.0f}".format(krw), "{:,.0f}".format(krw),
                   self.invest_ratio * 100, "{:,.0f}".format(amount), reason),
            )
            return False

        price = self._get_current_price()

        if self._upbit:
            result = self._upbit.buy_market_order(self.ticker, amount)
            if not result or "error" in result:
                logger.error("[매수 실패] %s", result)
                return False
            log_reason = reason
        else:
            log_reason = "[시뮬] " + reason

        # 공통 포지션 업데이트
        self.position.avg_price = price
        self.position.volume = amount / price
        self.position.entry_time = time.time()
        self.position.highest_price = price
        self.position.entry_atr = current_atr
        self._daily_trades += 1
        self.trade_logs.append(TradeLog(time.time(), "BUY", price, amount, reason))

        atr_info = " (ATR:%.0f)" % current_atr if current_atr > 0 else ""
        tag = "[매수]" if self._upbit else "[시뮬] 매수:"
        logger.info("%s %s | %.0f원 투자%s | %s", tag, self.ticker, amount, atr_info, reason)
        self.telegram.notify_buy(self.ticker, price, amount, log_reason)
        self.trade_logger.log(
            bot="coin_trader", side="BUY", symbol=self.ticker, exchange="upbit",
            price=price, quantity=amount / price, amount=amount,
            fee=amount * self.fee_rate, reason=reason,
            indicators=self._last_indicators)
        return True

    def _calc_pnl(self, sell_price: float) -> float:
        """수수료 포함 실수익률 계산 (공통 로직 위임)"""
        return self.calc_pnl(self.position.avg_price, sell_price)

    def _sell(self, reason: str, partial: bool = False, partial_pct: float = 0) -> bool:
        """전량 매도 또는 분할 매도. partial_pct: 현재 보유량의 N% 매도."""
        full_volume = self._get_coin_balance() if self._upbit else self.position.volume
        if full_volume <= 0:
            return False

        if partial:
            pct = partial_pct if partial_pct > 0 else self.partial_exit_pct
            sell_volume = full_volume * (pct / 100)
        else:
            sell_volume = full_volume

        price = self._get_current_price()
        pnl_pct = self._calc_pnl(price)

        tag = "[분할매도]" if partial else "[매도]"

        if self._upbit:
            result = self._upbit.sell_market_order(self.ticker, sell_volume)
            if result and "error" not in result:
                self._daily_trades += 1
                self.trade_logs.append(TradeLog(time.time(), "SELL", price, sell_volume * price, reason, pnl_pct))
                logger.info("%s %s | 수익률: %+.2f%% | %s", tag, self.ticker, pnl_pct, reason)
                self.telegram.notify_sell(self.ticker, price, pnl_pct, tag + " " + reason)
            else:
                logger.error("[매도 실패] %s", result)
                self.telegram.notify_error("매도 실패: %s\n코인: %s" % (result, self.ticker))
                return False
        else:
            self._daily_trades += 1
            self.trade_logs.append(TradeLog(time.time(), "SELL", price, sell_volume * price, reason, pnl_pct))
            logger.info("[시뮬] %s 수익률 %+.2f%% | %s", tag, pnl_pct, reason)
            self.telegram.notify_sell(self.ticker, price, pnl_pct, "[시뮬]" + tag + " " + reason)

        # CSV 기록 + Kill Switch
        pnl_amount = sell_volume * price * (pnl_pct / 100)
        self.trade_logger.log(
            bot="coin_trader", side="SELL", symbol=self.ticker, exchange="upbit",
            price=price, quantity=sell_volume, amount=sell_volume * price,
            fee=sell_volume * price * self.fee_rate,
            indicators=self._last_indicators,
            pnl_pct=pnl_pct, pnl_amount=pnl_amount, reason=reason,
        )
        if not partial:
            self.kill_switch.record_trade(pnl_amount)

        if partial:
            self.position.volume = full_volume - sell_volume
            self.position.partial_sold = True
            # 분할매도 후 최고가를 현재가로 리셋 → 트레일링 스톱 기준 갱신
            self.position.highest_price = price
        else:
            self._track_loss(pnl_pct)
            self.position = Position(ticker=self.ticker)
        return True

    def _track_loss(self, pnl_pct: float):
        """연속 손실 추적 및 쿨다운 발동 (공통 로직 위임 + 텔레그램)"""
        self.record_trade_result(pnl_pct)
        if self._consecutive_losses >= self._max_consecutive_losses:
            self.telegram.notify_cooldown(self._consecutive_losses, self._cooldown_minutes)

    def _alert_once(self, key: str, message: str, cooldown_sec: int = 300):
        """같은 종류의 알림은 5분에 1번만 전송"""
        now = time.time()
        if key == self._last_alert_reason and (now - self._last_alert_time) < cooldown_sec:
            return
        self._last_alert_reason = key
        self._last_alert_time = now
        self.telegram.send(message)

    def _is_cooled_down(self) -> bool:
        """공통 쿨다운 로직 위임"""
        return self.is_in_cooldown()

    # ── 손절/익절 ──

    def _check_stop_loss(self, current_price: float) -> bool:
        """v4: 보호적 손절 포함 (공통 로직 위임)"""
        return self.check_stop_loss(
            self.position.avg_price, current_price,
            self.position.entry_atr, self.position.partial_stage)

    def _check_trailing_stop(self, current_price: float) -> bool:
        """v4: ADX 적응형 트레일링 스톱 (공통 로직 위임)"""
        return self.check_trailing_stop(
            self.position.avg_price, current_price,
            self.position.highest_price, self.position.entry_atr,
            self.position.partial_stage)

    def _get_stop_loss_detail(self, current_price: float) -> str:
        """손절 상세 사유 (공통 로직 위임)"""
        return self.get_stop_loss_detail(
            self.position.avg_price, current_price,
            self.position.entry_atr, self.position.partial_stage)

    def _get_trailing_detail(self, current_price: float) -> str:
        """트레일링 스톱 상세 사유 (공통 로직 위임)"""
        return self.get_trailing_detail(
            self.position.avg_price, current_price,
            self.position.highest_price, self.position.entry_atr)

    # ── 메인 사이클 ──

    _INDICATOR_KEYS = ("rsi", "macd_hist", "adx", "atr", "vol_ratio")

    def _get_current_indicators(self, df) -> Dict:
        """현재 지표값 추출 (CSV 기록용)"""
        if df is None or df.empty:
            return {}
        last = df.iloc[-1]
        return {
            k: float(v) if pd.notna(v := last.get(k)) else 0
            for k in self._INDICATOR_KEYS
        }

    def run_once(self):
        """한 사이클 실행"""
        # 하루 1회 자동 학습 (CSV → JSON)
        self.auto_learn_if_needed("coin_trader")

        # Kill Switch 체크
        if self.kill_switch.is_killed():
            return

        self.api_guard.wait_if_needed()

        df = self._fetch_ohlcv()
        if df is None:
            self.api_guard.on_error(Exception("데이터 없음"))
            return
        self.api_guard.on_success()

        df = self.indicators.add_all(df)
        self._last_df = df  # v4: ADX 참조용 캐시
        self._last_indicators = self._get_current_indicators(df)
        current_price = self._get_current_price()

        # 상위 타임프레임 갱신 (5분마다)
        self._update_higher_timeframe()

        # 포지션 동기화 (API 키 있을 때)
        # 실제 잔고를 기준으로 하되, 분할매도 상태/최고가/ATR은 보존
        if self._upbit:
            api_volume = self._get_coin_balance()
            api_avg_price = self._get_avg_buy_price()
            self.position.volume = api_volume
            self.position.avg_price = api_avg_price
            # entry_atr, highest_price, partial_sold, entry_time은 보존

        is_holding = self.position.volume > 0 and self.position.avg_price > 0

        # 최고가 갱신 + 손절/분할매도/트레일링 체크
        if is_holding:
            self.position.update_highest(current_price)
            gain_pct = self._calc_pnl(current_price)

            # 손절 (최우선)
            if self._check_stop_loss(current_price):
                detail = self._get_stop_loss_detail(current_price)
                net_loss = self._calc_pnl(current_price)
                self.telegram.notify_stop_loss(self.ticker, current_price, abs(net_loss))
                self._sell(detail)
                self._last_stop_loss_time = time.time()
                self._last_sell_time = time.time()  # 버그수정: 손절도 매도 시간 기록
                return

            # v4: ATR 기반 동적 분할익절 (공통 로직)
            stage = self.position.partial_stage
            tp1, tp2 = self.get_partial_triggers(self.position.avg_price, self.position.entry_atr)

            if stage == 0 and gain_pct >= tp1:
                self._sell(
                    "1차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (gain_pct, tp1),
                    partial=True, partial_pct=30.0,
                )
                self.position.partial_stage = 1
                return

            if stage == 1 and gain_pct >= tp2:
                self._sell(
                    "2차 분할익절 (+%.1f%%, 기준:%.1f%%)" % (gain_pct, tp2),
                    partial=True, partial_pct=30.0,
                )
                self.position.partial_stage = 2
                return

            # ATR 기반 트레일링 스톱 (나머지 40% 물량)
            if self._check_trailing_stop(current_price):
                detail = self._get_trailing_detail(current_price)
                self.telegram.notify_take_profit(self.ticker, current_price, gain_pct)
                self._sell(detail)
                self._last_sell_time = time.time()
                return

        # 쿨다운 체크
        if self._is_cooled_down():
            remaining = int((self._cooldown_until - time.time()) / 60) + 1
            logger.debug("[쿨다운] %d분 남음 (연속%d손실)", remaining, self._consecutive_losses)
            return

        # 전략 분석
        sig = self.strategy.analyze(df)
        self._log_status(current_price, sig, is_holding, df)

        if not sig.is_actionable:
            return
        if self._daily_trades >= self._max_daily_trades:
            logger.debug("[제한] 일일 최대 거래 횟수 도달 (%d)", self._max_daily_trades)
            return

        now = time.time()

        if sig.signal == Signal.BUY and not is_holding:
            # v4: 공통 재진입 쿨다운 (수익/손실 구분)
            if self.check_rebuy_cooldown(now):
                return

            # 직전 매수가 근처(±1.0%)에서 재매수 방지 (v3.1: 0.5%→1.0%)
            if self._last_buy_price > 0:
                price_diff = abs(current_price - self._last_buy_price) / self._last_buy_price * 100
                if price_diff < 1.0:
                    logger.debug("[대기] 직전 매수가 근처 (%.1f%% 차이)", price_diff)
                    return

            # BTC 상관관계 체크 (알트코인만)
            corr = self.correlation.get_signal_modifier(self.ticker)
            if not corr["buy_allowed"]:
                logger.debug("[BTC연동] %s 매수 차단: %s", self.ticker, corr["reason"])
                return

            atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and pd.notna(df["atr"].iloc[-1]) else 0
            min_atr = current_price * 0.005
            if atr < min_atr:
                atr = min_atr

            # BTC 추세에 따라 신뢰도 보정
            if corr["confidence_boost"] != 0:
                sig = TradeSignal(
                    sig.signal,
                    min(1.0, max(0, sig.confidence + corr["confidence_boost"])),
                    sig.reason + " | " + corr["reason"],
                    sig.price,
                )
                if not sig.is_actionable:
                    return

            # v5: 세션 기반 신뢰도 보정
            if self._is_active_session():
                session_adj = self._session_conf_boost
            else:
                session_adj = -self._session_conf_penalty
            if session_adj != 0:
                adjusted_conf = min(1.0, max(0, sig.confidence + session_adj))
                sig = TradeSignal(sig.signal, adjusted_conf,
                                  sig.reason + (" | 활성세션" if session_adj > 0 else " | 비활성세션"),
                                  sig.price)
                if not sig.is_actionable:
                    logger.debug("[세션필터] 비활성 세션 → 신뢰도 부족")
                    return

            # v2: 학습 데이터 기반 신뢰도 보정
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
                self._last_buy_price = current_price

        elif sig.signal == Signal.SELL and is_holding:
            pnl_before = self._calc_pnl(current_price)
            if self._sell(sig.reason):
                self._last_sell_time = now
                self._last_sell_profitable = pnl_before > 0

    def _update_higher_timeframe(self):
        """상위 타임프레임 추세를 주기적으로 갱신"""
        now = time.time()
        if now - self._htf_last_update < self._htf_update_interval:
            return
        self._htf_last_update = now
        if hasattr(self.strategy, "set_higher_timeframe"):
            htf_map = {
                "minute1": "minute15",
                "minute3": "minute30",
                "minute5": "minute60",
                "minute15": "minute60",
                "minute30": "day",
                "minute60": "day",
            }
            htf_interval = htf_map.get(self.interval, "day")
            self.strategy.set_higher_timeframe(self.ticker, htf_interval)

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

        logger.debug(
            "[%s] 가격: %s%s | %s (%.0f%%)%s | %s",
            self.ticker, "{:,.0f}".format(price), adx_str,
            sig.signal.value, sig.confidence * 100,
            extra, sig.reason,
        )

    def start(self, poll_sec: int = 60):
        """무한 루프 실행"""
        self.running = True
        self._stop_event.clear()

        def _stop(signum, frame):
            logger.info("종료 시그널 수신...")
            self.running = False
            self._stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        logger.info("=" * 55)
        logger.info("  기술적 분석 자동매매 봇 시작")
        logger.info("  대상: %s | 전략: %s", self.ticker, self.strategy.name)
        logger.info("  투자비율: %.0f%% | 최대: %s원",
                     self.invest_ratio * 100, "{:,.0f}".format(self.max_invest_krw))
        logger.info("  수수료: 편도 %.2f%% / 왕복 %.2f%%", self.fee_rate * 100, self.round_trip_fee_pct)
        logger.info("  손절: ATR x%.1f (폴백: -%.1f%%, 수수료 포함)", self.atr_stop_multiplier, self.stop_loss_pct)
        logger.info("  익절: +%.1f%%에서 분할 → +%.1f%%부터 트레일링 %.1f%% (수수료 포함)",
                     self.take_profit_pct * 0.6, self.take_profit_pct, self.trailing_pct)
        logger.info("  보호: 연속%d손실→%d분쿨다운 | 일일-%.0f%%→당일매매중단",
                     self._max_consecutive_losses, self._cooldown_minutes,
                     self.kill_switch.max_daily_loss_pct)
        logger.info("  기록: logs/trades.csv")
        mode = "실거래" if self._upbit else "시뮬레이션"
        logger.info("  주기: %d초 | API: %s", poll_sec, mode)
        logger.info("=" * 55)
        self.telegram.notify_start(self.ticker, self.strategy.name, mode)

        while self.running:
            try:
                self.run_once()
                self._heartbeat()
                self._send_daily_report_if_needed()
            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                self._stop_event.wait(timeout=poll_sec)

        logger.info("봇 종료 완료")

    def _heartbeat(self):
        """매 시간 상태 로그 + 텔레그램 전송"""
        now = time.time()
        if now - self._last_heartbeat < 3600:
            return
        self._last_heartbeat = now

        hold_str = "보유 없음"
        if self.position.is_holding and self.position.avg_price > 0:
            price = self._get_current_price()
            pnl = self._calc_pnl(price) if price > 0 else 0
            hold_str = "보유: %s 평단:%s 수익:%+.1f%%" % (
                self.ticker, "{:,.0f}".format(self.position.avg_price), pnl)

        status = "[정기보고] %s | %s | 거래:%d건 | PnL:%+.0f원" % (
            self.ticker, hold_str, self._daily_trades, self.kill_switch.daily_pnl)
        logger.info(status)
        self.telegram.notify_heartbeat(
            self.ticker, hold_str, self._daily_trades, self.kill_switch.daily_pnl)

    def _send_daily_report_if_needed(self):
        """날짜가 바뀌면 전일 리포트를 텔레그램으로 전송"""
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
        logger.info("[일일리포트] %s 전송 완료", yesterday)
        self._last_report_date = today
