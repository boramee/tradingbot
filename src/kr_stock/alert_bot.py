"""한국 주식시장 공포/탐욕 알림봇"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from src.kr_stock.data_fetcher import KRStockDataFetcher
from src.kr_stock.fear_greed_index import (
    FearGreedCalculator,
    FearGreedResult,
    MarketFearGreedIndex,
    Sentiment,
)
from src.kr_stock.watchlist import Stock, WatchlistConfig
from src.utils.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)


class AlertThresholds:
    EXTREME_FEAR = 20
    FEAR = 40
    GREED = 60
    EXTREME_GREED = 80
    SCORE_CHANGE_ALERT = 10  # 이전 대비 이만큼 변동 시 알림


class KRStockAlertBot:
    """한국 주식시장 공포/탐욕 알림봇

    원칙: 시장이 탐욕적일 때 공포에 떨고, 시장이 공포에 떨 때 탐욕을 가져라
    """

    def __init__(
        self,
        watchlist: WatchlistConfig,
        telegram: Optional[TelegramNotifier] = None,
        poll_interval_min: int = 60,
    ):
        self.watchlist = watchlist
        self.telegram = telegram
        self.poll_interval_min = poll_interval_min
        self.fetcher = KRStockDataFetcher(lookback_days=400)
        self.calculator = FearGreedCalculator()
        self.market_index = MarketFearGreedIndex()
        self._prev_scores: Dict[str, float] = {}
        self._prev_market_score: Optional[float] = None

    def run_once(self) -> Dict[str, FearGreedResult]:
        """한 번 실행하여 모든 종목의 공포/탐욕 지수를 계산하고 알림을 보낸다."""
        logger.info("=" * 60)
        logger.info("한국 주식시장 공포/탐욕 분석 시작 (%s)", datetime.now().strftime("%Y-%m-%d %H:%M"))
        logger.info("=" * 60)

        all_items = self.watchlist.all_items
        results: Dict[str, FearGreedResult] = {}
        stock_names: Dict[str, str] = {}

        for item in all_items:
            logger.info("분석 중: %s (%s)", item.name, item.code)
            df = self.fetcher.fetch_ohlcv(item.code)
            if df is None:
                logger.warning("데이터 수집 실패: %s", item.name)
                continue

            result = self.calculator.calculate(df)
            if result is None:
                continue

            results[item.code] = result
            stock_names[item.code] = item.name
            time.sleep(0.5)

        if not results:
            logger.error("분석 가능한 종목이 없습니다.")
            return results

        market_result = self.market_index.calculate_composite(results)

        self._print_dashboard(results, stock_names, market_result)
        self._send_alerts(results, stock_names, market_result)

        for code, r in results.items():
            self._prev_scores[code] = r.score
        if market_result:
            self._prev_market_score = market_result.score

        return results

    def run_loop(self):
        """주기적으로 반복 실행"""
        logger.info("공포/탐욕 알림봇 시작 (주기: %d분)", self.poll_interval_min)
        self._send_startup_message()

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("봇 종료")
                break
            except Exception as e:
                logger.error("실행 오류: %s", e)
                if self.telegram:
                    self.telegram.notify_error(f"알림봇 오류: {e}")

            logger.info("다음 분석까지 %d분 대기...", self.poll_interval_min)
            try:
                time.sleep(self.poll_interval_min * 60)
            except KeyboardInterrupt:
                logger.info("봇 종료")
                break

    def _print_dashboard(
        self,
        results: Dict[str, FearGreedResult],
        names: Dict[str, str],
        market_result: Optional[FearGreedResult],
    ):
        from tabulate import tabulate

        print("\n" + "=" * 80)
        print("  한국 주식시장 공포/탐욕 대시보드")
        print("  분석 시간: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print("=" * 80)

        if market_result:
            bar = self._score_bar(market_result.score)
            print(f"\n  시장 종합 지수: {market_result.score:.1f}/100  {market_result.sentiment.value}")
            print(f"  {bar}")
            print(f"  → {market_result.action_signal}\n")

        headers = ["종목", "현재가", "등락률", "점수", "심리", "RSI", "MA괴리", "변동성", "거래량", "52주", "BB", "신호"]
        rows = []
        for code, r in sorted(results.items(), key=lambda x: x[1].score):
            rows.append([
                names.get(code, code),
                f"{r.current_price:,.0f}",
                f"{r.price_change_pct:+.2f}%",
                f"{r.score:.1f}",
                r.sentiment.value,
                f"{r.rsi_score:.0f}",
                f"{r.ma_deviation_score:.0f}",
                f"{r.volatility_score:.0f}",
                f"{r.volume_trend_score:.0f}",
                f"{r.high_low_score:.0f}",
                f"{r.bollinger_score:.0f}",
                r.emoji,
            ])

        print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))
        print("\n" + "-" * 80)
        print("  🟢🟢 극단적 공포 (매수 기회)  🟢 공포  ⚪ 중립  🔴 탐욕  🔴🔴 극단적 탐욕 (매도 고려)")
        print("-" * 80 + "\n")

    @staticmethod
    def _score_bar(score: float, width: int = 40) -> str:
        filled = int(score / 100 * width)
        bar = "█" * filled + "░" * (width - filled)
        labels = " 공포 ◄────────── 중립 ──────────► 탐욕"
        return f"  [{bar}] {score:.1f}\n  {labels}"

    def _send_alerts(
        self,
        results: Dict[str, FearGreedResult],
        names: Dict[str, str],
        market_result: Optional[FearGreedResult],
    ):
        if market_result:
            self._check_market_alert(market_result)

        for code, result in results.items():
            name = names.get(code, code)
            self._check_stock_alert(code, name, result)

    def _check_market_alert(self, result: FearGreedResult):
        if self._prev_market_score is not None:
            diff = abs(result.score - self._prev_market_score)
            if diff < AlertThresholds.SCORE_CHANGE_ALERT:
                if result.sentiment not in (Sentiment.EXTREME_FEAR, Sentiment.EXTREME_GREED):
                    return

        if result.sentiment == Sentiment.EXTREME_FEAR:
            msg = self._format_market_alert(result, "extreme_fear")
            logger.info("🟢🟢 시장 종합: 극단적 공포!")
            self._notify(msg)
        elif result.sentiment == Sentiment.EXTREME_GREED:
            msg = self._format_market_alert(result, "extreme_greed")
            logger.info("🔴🔴 시장 종합: 극단적 탐욕!")
            self._notify(msg)
        elif result.sentiment == Sentiment.FEAR:
            msg = self._format_market_alert(result, "fear")
            logger.info("🟢 시장 종합: 공포 구간")
            self._notify(msg)
        elif result.sentiment == Sentiment.GREED:
            msg = self._format_market_alert(result, "greed")
            logger.info("🔴 시장 종합: 탐욕 구간")
            self._notify(msg)

    def _check_stock_alert(self, code: str, name: str, result: FearGreedResult):
        prev = self._prev_scores.get(code)
        if prev is not None:
            diff = abs(result.score - prev)
            if diff < AlertThresholds.SCORE_CHANGE_ALERT:
                if result.sentiment not in (Sentiment.EXTREME_FEAR, Sentiment.EXTREME_GREED):
                    return

        if result.sentiment in (Sentiment.EXTREME_FEAR, Sentiment.EXTREME_GREED):
            msg = self._format_stock_alert(name, code, result)
            self._notify(msg)

    def _format_market_alert(self, result: FearGreedResult, alert_type: str) -> str:
        titles = {
            "extreme_fear": "🟢🟢 시장 극단적 공포 - 매수 기회!",
            "fear": "🟢 시장 공포 구간",
            "greed": "🔴 시장 탐욕 구간",
            "extreme_greed": "🔴🔴 시장 극단적 탐욕 - 매도 고려!",
        }
        return (
            f"<b>{titles[alert_type]}</b>\n\n"
            f"종합 지수: <b>{result.score:.1f}/100</b>\n"
            f"심리 상태: {result.sentiment.value}\n\n"
            f"세부 지표:\n"
            f"  RSI: {result.rsi_score:.0f}\n"
            f"  MA 괴리율: {result.ma_deviation_score:.0f}\n"
            f"  변동성: {result.volatility_score:.0f}\n"
            f"  거래량 추세: {result.volume_trend_score:.0f}\n"
            f"  52주 위치: {result.high_low_score:.0f}\n"
            f"  볼린저 %B: {result.bollinger_score:.0f}\n\n"
            f"→ {result.action_signal}"
        )

    def _format_stock_alert(self, name: str, code: str, result: FearGreedResult) -> str:
        emoji = result.emoji
        return (
            f"<b>{emoji} {name} ({code})</b>\n\n"
            f"현재가: {result.current_price:,.0f}원\n"
            f"등락률: {result.price_change_pct:+.2f}%\n"
            f"공포/탐욕 점수: <b>{result.score:.1f}/100</b>\n"
            f"심리: {result.sentiment.value}\n\n"
            f"→ {result.action_signal}"
        )

    def _send_startup_message(self):
        stock_names = [s.name for s in self.watchlist.stocks[:5]]
        etf_names = [e.name for e in self.watchlist.etfs[:3]]
        msg = (
            "<b>🤖 공포/탐욕 알림봇 시작</b>\n\n"
            f"감시 종목: {', '.join(stock_names)} 등 {len(self.watchlist.stocks)}개\n"
            f"감시 ETF: {', '.join(etf_names)} 등 {len(self.watchlist.etfs)}개\n"
            f"분석 주기: {self.poll_interval_min}분\n\n"
            "원칙: 탐욕일 때 공포, 공포일 때 탐욕!"
        )
        logger.info(msg.replace("<b>", "").replace("</b>", ""))
        self._notify(msg)

    def _notify(self, message: str):
        if self.telegram and self.telegram.enabled:
            self.telegram.send(message)


def analyze_stocks(
    stocks: List[Stock],
    fetcher: Optional[KRStockDataFetcher] = None,
) -> Dict[str, FearGreedResult]:
    """간편 분석 함수: 종목 목록을 받아 공포/탐욕 지수를 계산한다."""
    if fetcher is None:
        fetcher = KRStockDataFetcher()
    calculator = FearGreedCalculator()
    results: Dict[str, FearGreedResult] = {}

    for stock in stocks:
        df = fetcher.fetch_ohlcv(stock.code)
        if df is None:
            continue
        result = calculator.calculate(df)
        if result:
            results[stock.code] = result
        time.sleep(0.5)

    return results
