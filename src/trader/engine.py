"""삼성전자 자동매매 엔진

한국투자증권 API를 통해 삼성전자(005930) 자동매매를 수행.
기술적 지표 기반 전략으로 매수/매도 신호를 생성하고 주문을 실행.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import AppConfig
from src.api.kis_client import KISClient, Position, OrderResult
from src.indicators.technical import TechnicalIndicators
from src.strategies import create_strategy
from src.strategies.base import BaseStrategy, Signal, TradeSignal
from src.risk.manager import RiskManager, RiskConfig
from src.utils.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)


class TraderEngine:
    """삼성전자 자동매매 엔진"""

    def __init__(self, config: AppConfig, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self.stock_code = config.trading.stock_code
        self.stock_name = config.trading.stock_name

        self.kis = KISClient(config.kis)
        self.indicators = TechnicalIndicators()
        self.strategy: BaseStrategy = create_strategy(config.trading.strategy)

        risk_config = RiskConfig(
            max_buy_amount=config.trading.max_buy_amount,
            max_hold_qty=config.trading.max_hold_qty,
            stop_loss_pct=config.trading.stop_loss_pct,
            take_profit_pct=config.trading.take_profit_pct,
        )
        self.risk = RiskManager(risk_config)
        self.notifier = TelegramNotifier(config.telegram.token, config.telegram.chat_id)

        self._running = False

    def start(self):
        """자동매매 시작"""
        mode = "모의투자" if self.dry_run else "실전투자"
        logger.info("=" * 60)
        logger.info("삼성전자 자동매매 시작")
        logger.info("종목: %s (%s)", self.stock_name, self.stock_code)
        logger.info("전략: %s", self.strategy.name)
        logger.info("모드: %s", mode)
        logger.info("매매주기: %d초", self.config.trading.poll_interval_sec)
        logger.info("손절: %.1f%% / 익절: %.1f%%",
                     self.config.trading.stop_loss_pct, self.config.trading.take_profit_pct)
        logger.info("=" * 60)

        self.notifier.notify_start(
            f"{self.stock_name}({self.stock_code})",
            self.strategy.name,
            mode,
        )

        self._running = True
        while self._running:
            try:
                if self._is_trading_time():
                    self.run_once()
                else:
                    logger.debug("장 시간 외 — 대기 중")
            except KeyboardInterrupt:
                logger.info("사용자 중단 요청")
                break
            except Exception as e:
                logger.error("매매 루프 에러: %s", e, exc_info=True)
                self.notifier.notify_error(str(e))

            time.sleep(self.config.trading.poll_interval_sec)

        logger.info("자동매매 종료")

    def stop(self):
        self._running = False

    def run_once(self) -> Optional[TradeSignal]:
        """1회 매매 사이클 실행"""
        df = self._fetch_ohlcv()
        if df is None or df.empty or len(df) < 30:
            logger.warning("OHLCV 데이터 부족 (%d행)", len(df) if df is not None else 0)
            return None

        df = self.indicators.add_all(df)
        signal = self.strategy.analyze(df)
        current_price = self._get_current_price()
        position = self._get_position()

        logger.info("[%s] 현재가: %s원 | 신호: %s (%.1f) | %s",
                     self.stock_name,
                     f"{current_price:,}" if current_price else "N/A",
                     signal.signal.value,
                     signal.confidence,
                     signal.reason)

        if position and current_price:
            if self.risk.check_stop_loss(position.avg_price, current_price):
                return self._execute_sell(position, current_price, "손절")
            if self.risk.check_take_profit(position.avg_price, current_price):
                return self._execute_sell(position, current_price, "익절")

        if signal.is_actionable:
            if signal.signal == Signal.BUY and current_price:
                return self._execute_buy(signal, current_price, position)
            elif signal.signal == Signal.SELL and position:
                return self._execute_sell(position, current_price or 0, signal.reason)

        return signal

    def _execute_buy(self, signal: TradeSignal, price: int, position: Optional[Position]) -> TradeSignal:
        current_qty = position.qty if position else 0
        cash = self._get_cash_balance()

        ok, reason = self.risk.can_buy(price, current_qty, cash)
        if not ok:
            logger.info("매수 스킵: %s", reason)
            return TradeSignal(Signal.HOLD, 0.0, f"매수불가: {reason}", price)

        qty = self.risk.calculate_buy_qty(price, cash)
        if qty <= 0:
            logger.info("매수 가능 수량 0")
            return TradeSignal(Signal.HOLD, 0.0, "매수가능수량 0", price)

        if self.dry_run:
            logger.info("[모의] 매수: %s %d주 @ %s원 (사유: %s)",
                       self.stock_name, qty, f"{price:,}", signal.reason)
            self.risk.record_trade()
            self.notifier.notify_buy(
                f"{self.stock_name}({self.stock_code})",
                price, price * qty, f"[모의] {signal.reason}",
            )
            return signal

        result = self.kis.buy_market(self.stock_code, qty)
        if result.success:
            self.risk.record_trade()
            self.notifier.notify_buy(
                f"{self.stock_name}({self.stock_code})",
                price, price * qty, signal.reason,
            )
            logger.info("매수 완료: %d주 (주문번호: %s)", qty, result.order_no)
        else:
            logger.warning("매수 실패: %s", result.message)

        return signal

    def _execute_sell(self, position: Position, price: int, reason: str) -> TradeSignal:
        ok, msg = self.risk.can_sell(position.qty)
        if not ok:
            logger.info("매도 스킵: %s", msg)
            return TradeSignal(Signal.HOLD, 0.0, f"매도불가: {msg}", price)

        pnl_pct = self.risk.get_pnl_pct(position.avg_price, price)

        if self.dry_run:
            logger.info("[모의] 매도: %s %d주 @ %s원 (수익률: %+.2f%%, 사유: %s)",
                       self.stock_name, position.qty, f"{price:,}", pnl_pct, reason)
            pnl = (price - position.avg_price) * position.qty
            self.risk.record_trade(pnl)
            self.notifier.notify_sell(
                f"{self.stock_name}({self.stock_code})",
                price, pnl_pct, f"[모의] {reason}",
            )
            return TradeSignal(Signal.SELL, 1.0, reason, price)

        result = self.kis.sell_market(self.stock_code, position.qty)
        if result.success:
            pnl = (price - position.avg_price) * position.qty
            self.risk.record_trade(pnl)
            self.notifier.notify_sell(
                f"{self.stock_name}({self.stock_code})",
                price, pnl_pct, reason,
            )
            logger.info("매도 완료: %d주 (수익률: %+.2f%%, 주문번호: %s)",
                       position.qty, pnl_pct, result.order_no)
        else:
            logger.warning("매도 실패: %s", result.message)

        return TradeSignal(Signal.SELL, 1.0, reason, price)

    def _fetch_ohlcv(self) -> Optional[pd.DataFrame]:
        try:
            return self.kis.get_daily_ohlcv(self.stock_code, count=100)
        except Exception as e:
            logger.error("OHLCV 조회 실패: %s", e)
            return None

    def _get_current_price(self) -> Optional[int]:
        try:
            sp = self.kis.get_current_price(self.stock_code)
            return sp.price
        except Exception as e:
            logger.error("현재가 조회 실패: %s", e)
            return None

    def _get_position(self) -> Optional[Position]:
        try:
            return self.kis.get_stock_position(self.stock_code)
        except Exception as e:
            logger.error("잔고 조회 실패: %s", e)
            return None

    def _get_cash_balance(self) -> int:
        try:
            return self.kis.get_cash_balance()
        except Exception as e:
            logger.error("예수금 조회 실패: %s", e)
            return 0

    def _is_trading_time(self) -> bool:
        """장 시간인지 확인 (평일 09:00~15:30)"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False

        start_parts = self.config.trading.trading_start_time.split(":")
        end_parts = self.config.trading.trading_end_time.split(":")

        start_hour, start_min = int(start_parts[0]), int(start_parts[1])
        end_hour, end_min = int(end_parts[0]), int(end_parts[1])

        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min

        return start_minutes <= current_minutes <= end_minutes
