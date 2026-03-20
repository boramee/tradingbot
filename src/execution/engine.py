"""재정거래 실행 엔진 - 동시 주문 및 슬리피지 보호"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Optional

from config.settings import ArbitrageConfig
from src.exchanges.base_exchange import BaseExchange, OrderResult
from src.arbitrage.detector import ArbitrageOpportunity
from src.risk.manager import RiskManager
from src.monitor.fx_rate import FXRateProvider

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    opportunity: ArbitrageOpportunity
    buy_result: Optional[OrderResult] = None
    sell_result: Optional[OrderResult] = None
    actual_profit_usdt: float = 0.0
    success: bool = False
    error: str = ""

    def summary(self) -> str:
        status = "성공" if self.success else "실패"
        return (
            f"[실행 {status}] {self.opportunity.symbol} | "
            f"매수:{self.opportunity.buy_exchange} → 매도:{self.opportunity.sell_exchange} | "
            f"예상수익: {self.opportunity.net_profit_pct:.3f}% | "
            f"실현수익: {self.actual_profit_usdt:.4f} USDT"
            + (f" | 오류: {self.error}" if self.error else "")
        )


class ExecutionEngine:
    """
    재정거래 실행:
    1. 리스크 검증
    2. 매수/매도 거래소에서 동시에 주문
    3. 결과 확인 및 PnL 계산
    """

    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        risk_manager: RiskManager,
        config: ArbitrageConfig,
        fx_provider: FXRateProvider,
    ):
        self.exchanges = exchanges
        self.risk_manager = risk_manager
        self.config = config
        self.fx_provider = fx_provider
        self._dry_run = True  # 기본적으로 시뮬레이션 모드

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool):
        self._dry_run = value
        mode = "시뮬레이션" if value else "실거래"
        logger.warning("실행 모드 변경: %s", mode)

    def execute(self, opp: ArbitrageOpportunity) -> ExecutionResult:
        """재정거래 기회를 실행"""

        ok, reason = self.risk_manager.validate_opportunity(opp)
        if not ok:
            logger.debug("리스크 검증 실패: %s - %s", opp.symbol, reason)
            return ExecutionResult(opportunity=opp, error=reason)

        buy_ex = self.exchanges.get(opp.buy_exchange)
        sell_ex = self.exchanges.get(opp.sell_exchange)
        if not buy_ex or not sell_ex:
            return ExecutionResult(
                opportunity=opp,
                error=f"거래소 없음: {opp.buy_exchange} 또는 {opp.sell_exchange}",
            )

        trade_usdt = self.risk_manager.calculate_trade_amount(opp)
        if trade_usdt < 1:
            return ExecutionResult(opportunity=opp, error="거래 금액 너무 작음")

        if buy_ex.is_korean:
            buy_amount = trade_usdt * self.fx_provider.get_krw_per_usdt()
        else:
            buy_amount = trade_usdt

        sell_amount_base = trade_usdt / opp.sell_price_usdt if opp.sell_price_usdt > 0 else 0

        if self._dry_run:
            return self._simulate(opp, trade_usdt, buy_amount, sell_amount_base)

        return self._execute_real(opp, buy_ex, sell_ex, buy_amount, sell_amount_base, trade_usdt)

    def _simulate(
        self, opp: ArbitrageOpportunity, trade_usdt: float,
        buy_amount: float, sell_amount_base: float,
    ) -> ExecutionResult:
        """시뮬레이션 모드 - 실제 주문 없이 예상 결과 계산"""
        estimated_profit = trade_usdt * (opp.net_profit_pct / 100)

        self.risk_manager.on_trade_start(opp)
        self.risk_manager.on_trade_complete(opp, estimated_profit)

        logger.info(
            "[시뮬레이션] %s | %s에서 %.4f USDT 매수 → %s에서 매도 | "
            "예상 순수익: %.4f USDT (%.3f%%)",
            opp.symbol, opp.buy_exchange, trade_usdt,
            opp.sell_exchange, estimated_profit, opp.net_profit_pct,
        )

        return ExecutionResult(
            opportunity=opp,
            actual_profit_usdt=estimated_profit,
            success=True,
        )

    def _execute_real(
        self,
        opp: ArbitrageOpportunity,
        buy_ex: BaseExchange,
        sell_ex: BaseExchange,
        buy_amount_quote: float,
        sell_amount_base: float,
        trade_usdt: float,
    ) -> ExecutionResult:
        """실제 주문 실행 - 매수/매도 동시 주문"""
        self.risk_manager.on_trade_start(opp)

        buy_result = None
        sell_result = None

        with ThreadPoolExecutor(max_workers=2) as executor:
            buy_future = executor.submit(buy_ex.buy_market, opp.symbol, buy_amount_quote)
            sell_future = executor.submit(sell_ex.sell_market, opp.symbol, sell_amount_base)

            try:
                buy_result = buy_future.result(timeout=15)
            except Exception as e:
                logger.error("매수 주문 실패: %s", e)

            try:
                sell_result = sell_future.result(timeout=15)
            except Exception as e:
                logger.error("매도 주문 실패: %s", e)

        buy_ok = buy_result and buy_result.success
        sell_ok = sell_result and sell_result.success
        success = buy_ok and sell_ok

        actual_profit = 0.0
        error = ""

        if success:
            actual_profit = trade_usdt * (opp.net_profit_pct / 100) * 0.8  # 보수적 추정
        elif buy_ok and not sell_ok:
            error = f"매도 실패 (매수는 완료됨) - 수동 확인 필요! {sell_result.error if sell_result else ''}"
            logger.critical("[경고] 한쪽만 체결됨: %s 매수 완료, %s 매도 실패",
                            opp.buy_exchange, opp.sell_exchange)
        elif sell_ok and not buy_ok:
            error = f"매수 실패 (매도는 완료됨) - 수동 확인 필요! {buy_result.error if buy_result else ''}"
            logger.critical("[경고] 한쪽만 체결됨: %s 매도 완료, %s 매수 실패",
                            opp.sell_exchange, opp.buy_exchange)
        else:
            error = "양쪽 주문 모두 실패"

        self.risk_manager.on_trade_complete(opp, actual_profit)

        result = ExecutionResult(
            opportunity=opp,
            buy_result=buy_result,
            sell_result=sell_result,
            actual_profit_usdt=actual_profit,
            success=success,
            error=error,
        )

        if error:
            logger.error(result.summary())
        else:
            logger.info(result.summary())

        return result
