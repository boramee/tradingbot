"""거래소 간 재정거래(아비트라지) 자동매매 봇 메인 모듈"""

import logging
import signal
import sys
import time

from config.settings import AppConfig
from src.exchanges.exchange_factory import create_all_exchanges
from src.monitor.fx_rate import FXRateProvider
from src.monitor.price_monitor import PriceMonitor
from src.arbitrage.detector import ArbitrageDetector
from src.execution.engine import ExecutionEngine
from src.risk.manager import RiskManager
from src.utils.logger import setup_logger
from src.utils.dashboard import Dashboard

logger = logging.getLogger(__name__)


class ArbitrageBot:
    """거래소 간 재정거래 자동매매 봇"""

    def __init__(self, config: AppConfig = None, live: bool = False):
        self.config = config or AppConfig()
        self.running = False

        setup_logger(self.config.log_level)

        logger.info("=" * 60)
        logger.info("  거래소 간 재정거래 봇 시작")
        logger.info("=" * 60)

        self.exchanges = create_all_exchanges(self.config)
        if not self.exchanges:
            logger.error("연결된 거래소가 없습니다!")
            sys.exit(1)

        logger.info("연결된 거래소: %s", ", ".join(self.exchanges.keys()))
        logger.info("모니터링 코인: %s", ", ".join(self.config.arbitrage.target_symbols))

        self.fx_provider = FXRateProvider()
        self.price_monitor = PriceMonitor(
            self.exchanges, self.fx_provider, self.config.arbitrage.target_symbols,
        )

        fee_rates = {name: ex.fee_rate for name, ex in self.exchanges.items()}
        self.detector = ArbitrageDetector(self.config.arbitrage, fee_rates)

        self.risk_manager = RiskManager(self.config.arbitrage)
        self.execution = ExecutionEngine(
            self.exchanges, self.risk_manager, self.config.arbitrage, self.fx_provider,
        )
        self.execution.dry_run = not live

        self.dashboard = Dashboard()

        mode = "실거래" if live else "시뮬레이션"
        logger.info("실행 모드: %s", mode)
        logger.info("최소 수익률: %.2f%%", self.config.arbitrage.min_profit_pct)
        logger.info("최대 슬리피지: %.2f%%", self.config.arbitrage.max_slippage_pct)
        logger.info("1회 최대 거래: %.0f USDT", self.config.arbitrage.max_trade_usdt)

    def run_once(self) -> dict:
        """한 사이클: 가격 조회 → 기회 탐지 → 실행"""
        try:
            snapshots = self.price_monitor.fetch_all_prices()
            all_opportunities = self.detector.detect_all(snapshots)
            profitable = self.detector.detect_profitable(snapshots)

            for opp in profitable:
                result = self.execution.execute(opp)
                if result.success:
                    logger.info("거래 실행: %s", result.summary())

            return {
                "snapshots": snapshots,
                "all_opportunities": all_opportunities,
                "profitable": profitable,
            }
        except Exception as e:
            logger.error("사이클 오류: %s", e, exc_info=True)
            return {"snapshots": {}, "all_opportunities": [], "profitable": []}

    def start(self, show_dashboard: bool = True):
        """봇 시작 (무한 루프)"""
        self.running = True

        def _stop(signum, frame):
            logger.info("종료 시그널 수신...")
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        interval = self.config.arbitrage.poll_interval_sec
        logger.info("모니터링 시작 (주기: %d초)", interval)

        while self.running:
            cycle_start = time.time()

            result = self.run_once()

            if show_dashboard:
                fx_rate = self.fx_provider.get_krw_per_usdt()
                self.dashboard.render(
                    snapshots=result["snapshots"],
                    opportunities=result["all_opportunities"],
                    daily_pnl=self.risk_manager.daily_pnl,
                    trade_count=self.risk_manager.trade_count_today,
                    fx_rate=fx_rate,
                )

            elapsed = time.time() - cycle_start
            sleep_time = max(0, interval - elapsed)
            if self.running and sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("봇 종료 완료")


def main():
    live = "--live" in sys.argv
    no_dashboard = "--no-dashboard" in sys.argv

    if live:
        print("⚠️  실거래 모드로 실행합니다! 실제 자금이 사용됩니다.")
        print("   5초 후 시작... (Ctrl+C로 취소)")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("취소됨")
            return

    config = AppConfig()
    bot = ArbitrageBot(config, live=live)
    bot.start(show_dashboard=not no_dashboard)


if __name__ == "__main__":
    main()
