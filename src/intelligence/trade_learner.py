"""승률 학습 엔진 - 과거 거래 CSV를 분석하여 패턴 학습

trades.csv에서:
  1. 어떤 RSI 범위에서 승률이 높았는지
  2. 어떤 ADX 범위에서 승률이 높았는지
  3. 어떤 거래량 비율에서 승률이 높았는지
  4. 어떤 시간대에 승률이 높았는지

→ 학습된 최적 파라미터로 필터를 자동 조정
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LearnedParams:
    """학습된 최적 파라미터"""
    best_rsi_buy_range: tuple = (25, 35)     # RSI 매수 적정 구간
    best_rsi_sell_range: tuple = (65, 75)
    min_adx: float = 10
    min_vol_ratio: float = 0.3
    best_hours: List[int] = None             # 승률 높은 시간대
    win_rate: float = 0
    total_trades: int = 0
    avg_win: float = 0
    avg_loss: float = 0
    notes: str = ""

    def __post_init__(self):
        if self.best_hours is None:
            self.best_hours = []

    def summary(self) -> str:
        return (
            "학습 결과 (%d건 분석)\n"
            "  승률: %.1f%% | 평균수익: %+.2f%% | 평균손실: %.2f%%\n"
            "  최적 RSI 매수: %d~%d | 매도: %d~%d\n"
            "  최소 ADX: %.0f | 최소 거래량: %.1fx\n"
            "  최적 시간대: %s\n"
            "  %s"
        ) % (
            self.total_trades, self.win_rate, self.avg_win, self.avg_loss,
            self.best_rsi_buy_range[0], self.best_rsi_buy_range[1],
            self.best_rsi_sell_range[0], self.best_rsi_sell_range[1],
            self.min_adx, self.min_vol_ratio,
            ", ".join("%d시" % h for h in self.best_hours[:5]) if self.best_hours else "데이터 부족",
            self.notes,
        )


class TradeLearner:
    """과거 거래에서 최적 파라미터를 학습"""

    def __init__(self, csv_path: str = "logs/trades.csv"):
        self._path = csv_path

    def learn(self, bot_filter: str = "") -> LearnedParams:
        """CSV에서 학습"""
        trades = self._load(bot_filter)
        if len(trades) < 5:
            return LearnedParams(notes="거래 %d건 (최소 5건 필요)" % len(trades))

        sells = [t for t in trades if t["side"] in ("SELL", "ARB")]
        if len(sells) < 3:
            return LearnedParams(notes="매도 %d건 (최소 3건 필요)" % len(sells))

        params = LearnedParams()
        params.total_trades = len(sells)

        wins = [t for t in sells if float(t.get("pnl_pct", 0)) > 0]
        losses = [t for t in sells if float(t.get("pnl_pct", 0)) <= 0]

        params.win_rate = len(wins) / len(sells) * 100
        params.avg_win = sum(float(t["pnl_pct"]) for t in wins) / len(wins) if wins else 0
        params.avg_loss = sum(float(t["pnl_pct"]) for t in losses) / len(losses) if losses else 0

        # RSI 분석: 승리 거래의 RSI 분포
        win_rsis = [float(t.get("rsi", 0)) for t in wins if float(t.get("rsi", 0)) > 0]
        loss_rsis = [float(t.get("rsi", 0)) for t in losses if float(t.get("rsi", 0)) > 0]

        if win_rsis:
            avg_win_rsi = sum(win_rsis) / len(win_rsis)
            # 승리 거래의 RSI 중심으로 ±10 구간
            params.best_rsi_buy_range = (max(10, int(avg_win_rsi - 10)), min(50, int(avg_win_rsi + 10)))

        # ADX 분석
        win_adx = [float(t.get("adx", 0)) for t in wins if float(t.get("adx", 0)) > 0]
        loss_adx = [float(t.get("adx", 0)) for t in losses if float(t.get("adx", 0)) > 0]

        if win_adx and loss_adx:
            avg_win_adx = sum(win_adx) / len(win_adx)
            avg_loss_adx = sum(loss_adx) / len(loss_adx)
            params.min_adx = max(5, (avg_win_adx + avg_loss_adx) / 2 - 5)

        # 거래량 분석
        win_vols = [float(t.get("volume_ratio", 0)) for t in wins if float(t.get("volume_ratio", 0)) > 0]
        if win_vols:
            params.min_vol_ratio = max(0.2, min(win_vols) * 0.8)

        # 시간대 분석
        hour_stats: Dict[int, Dict] = {}
        for t in sells:
            dt = t.get("datetime", "")
            if len(dt) >= 13:
                try:
                    hour = int(dt[11:13])
                    if hour not in hour_stats:
                        hour_stats[hour] = {"wins": 0, "total": 0}
                    hour_stats[hour]["total"] += 1
                    if float(t.get("pnl_pct", 0)) > 0:
                        hour_stats[hour]["wins"] += 1
                except ValueError:
                    pass

        best_hours = []
        for h, s in sorted(hour_stats.items()):
            if s["total"] >= 2:
                wr = s["wins"] / s["total"] * 100
                if wr >= 60:
                    best_hours.append(h)
        params.best_hours = best_hours

        params.notes = "학습 완료"
        return params

    def _load(self, bot_filter: str) -> List[Dict]:
        if not os.path.exists(self._path):
            return []
        trades = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if bot_filter and row.get("bot", "") != bot_filter:
                        continue
                    trades.append(row)
        except Exception:
            pass
        return trades

    def get_recommendation(self, bot_filter: str = "") -> str:
        """학습 결과를 사람이 읽을 수 있는 추천으로 변환"""
        params = self.learn(bot_filter)
        return params.summary()
