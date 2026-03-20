"""자동매매 봇 메인 실행 모듈"""

import logging
import signal
import sys
import time
from typing import Optional

from config.settings import AppConfig
from src.exchange.upbit_client import UpbitClient
from src.indicators.technical import TechnicalIndicators
from src.strategies.base_strategy import BaseStrategy, Signal
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.macd_strategy import MACDStrategy
from src.strategies.bollinger_strategy import BollingerStrategy
from src.strategies.combined_strategy import CombinedStrategy
from src.risk.manager import RiskManager
from src.utils.logger import setup_logger

logger = logging.getLogger(__name__)


class TradingBot:
    """자동매매 봇 메인 엔진"""

    STRATEGY_MAP = {
        "rsi": RSIStrategy,
        "macd": MACDStrategy,
        "bollinger": BollingerStrategy,
        "combined": CombinedStrategy,
    }

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self.running = False

        setup_logger(self.config.log_level)

        self.client = UpbitClient(self.config.exchange, self.config.trading)
        self.indicators = TechnicalIndicators(self.config.indicator)
        self.risk_manager = RiskManager(self.config.risk)
        self.strategy = self._create_strategy()

        logger.info("=" * 50)
        logger.info("  업비트 자동매매 봇 초기화")
        logger.info("  대상: %s", self.config.trading.ticker)
        logger.info("  전략: %s", self.strategy.name)
        logger.info("  투자비율: %.0f%%", self.config.trading.investment_ratio * 100)
        logger.info("  최대투자: %s원", f"{self.config.trading.max_investment_krw:,.0f}")
        logger.info("  손절: %.1f%% / 익절: %.1f%%",
                     self.config.risk.stop_loss_pct, self.config.risk.take_profit_pct)
        logger.info("=" * 50)

    def _create_strategy(self) -> BaseStrategy:
        strategy_name = self.config.trading.strategy.lower()
        cls = self.STRATEGY_MAP.get(strategy_name, CombinedStrategy)
        return cls(self.config.indicator)

    def _analyze_market(self) -> Optional[dict]:
        """시장 데이터를 수집하고 분석"""
        df = self.client.get_ohlcv(count=self.config.trading.candle_count)
        if df is None:
            logger.warning("시장 데이터를 가져올 수 없습니다.")
            return None

        df = self.indicators.add_all_indicators(df)
        signal = self.strategy.analyze(df)

        current_price = self.client.get_current_price()
        avg_buy_price = self.client.get_avg_buy_price()
        holding_volume = self.client.get_holding_volume()
        krw_balance = self.client.get_balance("KRW")
        holding_value = holding_volume * current_price

        validated_signal = self.risk_manager.validate_signal(
            signal, avg_buy_price, current_price, krw_balance, holding_value
        )

        return {
            "signal": validated_signal,
            "current_price": current_price,
            "avg_buy_price": avg_buy_price,
            "holding_volume": holding_volume,
            "krw_balance": krw_balance,
            "holding_value": holding_value,
        }

    def _execute_trade(self, analysis: dict):
        """분석 결과에 따라 매매 실행"""
        signal = analysis["signal"]

        if not signal.is_actionable:
            logger.info("[관망] %s (신뢰도: %.2f)", signal.reason, signal.confidence)
            return

        if signal.signal == Signal.BUY:
            amount = self.client.get_investment_amount()
            if amount >= 5000:
                logger.info("[매수] %.0f원 투자 - %s", amount, signal.reason)
                result = self.client.buy_market_order(amount=amount)
                if result:
                    self.risk_manager.record_trade(True)
            else:
                logger.info("[매수 불가] 투자 가능 금액 부족: %.0f원", amount)

        elif signal.signal == Signal.SELL:
            volume = analysis["holding_volume"]
            if volume > 0:
                avg_price = analysis["avg_buy_price"]
                current_price = analysis["current_price"]
                is_profit = current_price > avg_price if avg_price > 0 else True

                logger.info("[매도] %.8f개 - %s", volume, signal.reason)
                result = self.client.sell_market_order(volume=volume)
                if result:
                    self.risk_manager.record_trade(is_profit)
            else:
                logger.info("[매도 불가] 보유 수량 없음")

    def run_once(self):
        """한 사이클 실행 (분석 → 판단 → 실행)"""
        try:
            analysis = self._analyze_market()
            if analysis:
                self._log_status(analysis)
                self._execute_trade(analysis)
        except Exception as e:
            logger.error("매매 사이클 오류: %s", e, exc_info=True)

    def _log_status(self, analysis: dict):
        """현재 상태 로깅"""
        logger.info(
            "[상태] 현재가: %s원 | KRW: %s원 | 보유량: %.8f | 보유가치: %s원",
            f"{analysis['current_price']:,.0f}",
            f"{analysis['krw_balance']:,.0f}",
            analysis["holding_volume"],
            f"{analysis['holding_value']:,.0f}",
        )

    def start(self, interval_seconds: int = 60):
        """봇 실행 (무한 루프)"""
        self.running = True

        def _signal_handler(signum, frame):
            logger.info("종료 시그널 수신. 봇을 안전하게 종료합니다...")
            self.running = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info("자동매매 봇 시작 (주기: %d초)", interval_seconds)

        while self.running:
            self.run_once()
            if self.running:
                logger.debug("다음 사이클까지 %d초 대기", interval_seconds)
                for _ in range(interval_seconds):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("자동매매 봇 종료 완료")

    def stop(self):
        """봇 정지"""
        self.running = False


def main():
    config = AppConfig()

    if not config.exchange.is_valid:
        print("=" * 50)
        print("  경고: API 키가 설정되지 않았습니다!")
        print("  .env 파일을 생성하고 API 키를 입력하세요.")
        print("  (.env.example 파일을 참고하세요)")
        print("=" * 50)
        print()

    bot = TradingBot(config)

    if "--once" in sys.argv:
        bot.run_once()
    else:
        interval = 60
        for i, arg in enumerate(sys.argv):
            if arg == "--interval" and i + 1 < len(sys.argv):
                interval = int(sys.argv[i + 1])
        bot.start(interval_seconds=interval)


if __name__ == "__main__":
    main()
