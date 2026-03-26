"""일일 수익률 리포트 - 매일 자정에 텔레그램으로 전송"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DailyReport:
    """trades.csv에서 당일 거래를 집계하여 리포트 생성"""

    LOCK_DIR = "logs"

    def __init__(self, csv_path: str = "logs/trades.csv"):
        self._path = csv_path

    def already_sent(self, target_date: str) -> bool:
        """다른 봇 프로세스가 이미 해당 날짜 리포트를 전송했는지 확인"""
        lock = os.path.join(self.LOCK_DIR, ".report_sent_%s" % target_date)
        if os.path.exists(lock):
            return True
        try:
            os.makedirs(self.LOCK_DIR, exist_ok=True)
            with open(lock, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        return False

    def generate(self, target_date: Optional[str] = None) -> str:
        """특정 날짜의 수익 리포트 생성. 기본=오늘."""
        if target_date is None:
            target_date = date.today().isoformat()

        trades = self._read_trades(target_date)
        if not trades:
            return "<b>📋 일일 리포트 (%s)</b>\n거래 없음" % target_date

        # 봇별 집계
        by_bot: Dict[str, List[Dict]] = {}
        for t in trades:
            bot = t.get("bot", "unknown")
            by_bot.setdefault(bot, []).append(t)

        lines = ["<b>📋 일일 리포트 (%s)</b>" % target_date, ""]
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0

        for bot, bot_trades in sorted(by_bot.items()):
            sells = [t for t in bot_trades if t["side"] in ("SELL", "ARB")]
            buys = [t for t in bot_trades if t["side"] == "BUY"]

            pnl_sum = sum(float(t.get("pnl_amount", 0)) for t in sells)
            win_count = sum(1 for t in sells if float(t.get("pnl_amount", 0)) > 0)
            loss_count = sum(1 for t in sells if float(t.get("pnl_amount", 0)) < 0)

            total_pnl += pnl_sum
            total_trades += len(sells)
            total_wins += win_count

            bot_label = {
                "coin_trader": "코인",
                "cross_arb": "재정거래",
                "stock_trader": "주식",
            }.get(bot, bot)

            if sells:
                avg_pnl = sum(float(t.get("pnl_pct", 0)) for t in sells) / len(sells)
                best = max(sells, key=lambda t: float(t.get("pnl_pct", 0)))
                worst = min(sells, key=lambda t: float(t.get("pnl_pct", 0)))

                lines.append("<b>%s</b>" % bot_label)
                lines.append("  매수: %d건 | 매도: %d건" % (len(buys), len(sells)))
                lines.append("  승: %d | 패: %d | 승률: %.0f%%" % (
                    win_count, loss_count,
                    win_count / len(sells) * 100 if sells else 0))
                lines.append("  수익: %s원 (평균: %+.2f%%)" % (
                    "{:+,.0f}".format(pnl_sum), avg_pnl))
                lines.append("  최고: %s %+.2f%%" % (
                    best.get("symbol", ""), float(best.get("pnl_pct", 0))))
                lines.append("  최저: %s %+.2f%%" % (
                    worst.get("symbol", ""), float(worst.get("pnl_pct", 0))))
                lines.append("")

        # 전체 합계
        win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
        emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➡️"
        lines.append("<b>%s 전체 합계</b>" % emoji)
        lines.append("  총 거래: %d건 | 승률: %.0f%%" % (total_trades, win_rate))
        lines.append("  <b>총 수익: %s원</b>" % "{:+,.0f}".format(total_pnl))

        return "\n".join(lines)

    def _read_trades(self, target_date: str) -> List[Dict]:
        if not os.path.exists(self._path):
            return []
        trades = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dt = row.get("datetime", "")
                    if dt.startswith(target_date):
                        trades.append(row)
        except Exception as e:
            logger.debug("CSV 읽기 실패: %s", e)
        return trades
