"""승률 학습 엔진 v2 — 과거 거래 CSV 분석 → 실전 매매 신뢰도 보정

v1 → v2 변경점:
  - 학습 결과를 JSON으로 저장/로드 → 봇 재시작 후에도 학습 유지
  - confidence_modifier() 메서드: 현재 지표 vs 학습 데이터 비교 → 신뢰도 보정
  - 표본 크기 가중: 데이터 적으면 보정 폭 축소 (과적합 방지)
  - Profit Factor 계산: 단순 승률보다 정확한 전략 품질 평가
  - 지표별 구간 분석: RSI/ADX/거래량을 구간별로 세분화
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LearnedParams:
    """학습된 최적 파라미터"""
    best_rsi_buy_range: tuple = (25, 35)
    best_rsi_sell_range: tuple = (65, 75)
    min_adx: float = 10
    min_vol_ratio: float = 0.3
    best_hours: List[int] = field(default_factory=list)
    worst_hours: List[int] = field(default_factory=list)
    win_rate: float = 0
    total_trades: int = 0
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float = 0
    notes: str = ""

    # v2: 지표 구간별 승률 (신뢰도 보정용)
    rsi_win_rates: Dict[str, float] = field(default_factory=dict)
    adx_win_rates: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        pf_str = "%.2f" % self.profit_factor if self.profit_factor < 100 else "∞"
        lines = [
            "학습 결과 (%d건 분석)" % self.total_trades,
            "  승률: %.1f%% | Profit Factor: %s" % (self.win_rate, pf_str),
            "  평균수익: %+.2f%% | 평균손실: %.2f%%" % (self.avg_win, self.avg_loss),
            "  최적 RSI 매수: %d~%d | 매도: %d~%d" % (
                self.best_rsi_buy_range[0], self.best_rsi_buy_range[1],
                self.best_rsi_sell_range[0], self.best_rsi_sell_range[1]),
            "  최소 ADX: %.0f | 최소 거래량: %.1fx" % (self.min_adx, self.min_vol_ratio),
        ]
        if self.best_hours:
            lines.append("  고승률 시간: %s" % ", ".join("%d시" % h for h in self.best_hours[:5]))
        if self.worst_hours:
            lines.append("  저승률 시간: %s" % ", ".join("%d시" % h for h in self.worst_hours[:5]))
        if self.rsi_win_rates:
            lines.append("  RSI 구간 승률: %s" % " | ".join(
                "%s:%.0f%%" % (k, v) for k, v in sorted(self.rsi_win_rates.items())))
        if self.adx_win_rates:
            lines.append("  ADX 구간 승률: %s" % " | ".join(
                "%s:%.0f%%" % (k, v) for k, v in sorted(self.adx_win_rates.items())))
        lines.append("  %s" % self.notes)
        return "\n".join(lines)


class TradeLearner:
    """과거 거래에서 최적 파라미터를 학습 + 실전 신뢰도 보정"""

    PARAMS_FILE = "logs/learned_params.json"

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

        # Profit Factor: 총수익 / 총손실 (1.0 이상이면 수익성)
        total_wins = sum(float(t["pnl_pct"]) for t in wins) if wins else 0
        total_losses = abs(sum(float(t["pnl_pct"]) for t in losses)) if losses else 0
        params.profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # RSI 구간별 승률 분석
        params.rsi_win_rates = self._bucket_win_rate(sells, "rsi", [
            ("0-30", 0, 30), ("30-50", 30, 50), ("50-70", 50, 70), ("70-100", 70, 100)])

        if params.rsi_win_rates:
            best_rsi = max(params.rsi_win_rates, key=params.rsi_win_rates.get)
            parts = best_rsi.split("-")
            if len(parts) == 2:
                params.best_rsi_buy_range = (int(parts[0]), int(parts[1]))

        # ADX 구간별 승률 분석
        params.adx_win_rates = self._bucket_win_rate(sells, "adx", [
            ("0-15", 0, 15), ("15-25", 15, 25), ("25-40", 25, 40), ("40+", 40, 200)])

        if params.adx_win_rates:
            best_adx = max(params.adx_win_rates, key=params.adx_win_rates.get)
            parts = best_adx.replace("+", "-200").split("-")
            if len(parts) >= 1:
                params.min_adx = max(5, float(parts[0]))

        # 거래량 분석
        win_vols = [float(t.get("volume_ratio", 0)) for t in wins if float(t.get("volume_ratio", 0)) > 0]
        if win_vols:
            params.min_vol_ratio = max(0.2, min(win_vols) * 0.8)

        # 시간대 분석 (고승률 + 저승률 시간대)
        hour_stats = self._hour_analysis(sells)
        params.best_hours = [h for h, wr in hour_stats.items() if wr >= 60]
        params.worst_hours = [h for h, wr in hour_stats.items() if wr < 40]

        params.notes = "학습 완료"
        return params

    def learn_and_save(self, bot_filter: str = "") -> LearnedParams:
        """학습 후 JSON으로 저장 (봇이 다음 시작 시 로드)"""
        params = self.learn(bot_filter)
        if params.total_trades >= 5:
            self._save_params(params)
        return params

    def confidence_modifier(self, rsi: float = 0, adx: float = 0,
                            vol_ratio: float = 0, hour: int = -1) -> float:
        """v2: 현재 지표와 학습 데이터 비교 → 신뢰도 보정값 반환

        반환값: -0.15 ~ +0.15 (매매 신호 confidence에 더함)
        표본 적으면 보정 폭 축소 (과적합 방지)
        """
        params = self.load_params()
        if params is None or params.total_trades < 10:
            return 0.0  # 데이터 부족 → 보정 없음

        # 표본 크기 가중: 거래 많을수록 보정 확신도 ↑
        # 10건→0.3, 30건→0.7, 50건+→1.0
        confidence_weight = min(1.0, params.total_trades / 50)
        modifier = 0.0

        # RSI 구간 보정
        if rsi > 0 and params.rsi_win_rates:
            for bucket, wr in params.rsi_win_rates.items():
                parts = bucket.replace("+", "-200").split("-")
                lo, hi = float(parts[0]), float(parts[1])
                if lo <= rsi < hi:
                    # 승률 60% 이상 → 보너스, 40% 이하 → 패널티
                    if wr >= 60:
                        modifier += 0.05
                    elif wr < 40:
                        modifier -= 0.05
                    break

        # ADX 구간 보정
        if adx > 0 and params.adx_win_rates:
            for bucket, wr in params.adx_win_rates.items():
                parts = bucket.replace("+", "-200").split("-")
                lo, hi = float(parts[0]), float(parts[1])
                if lo <= adx < hi:
                    if wr >= 60:
                        modifier += 0.05
                    elif wr < 40:
                        modifier -= 0.05
                    break

        # 시간대 보정
        if hour >= 0:
            if hour in params.best_hours:
                modifier += 0.05
            elif hour in params.worst_hours:
                modifier -= 0.05

        # 표본 크기 가중 적용
        return max(-0.15, min(0.15, modifier * confidence_weight))

    def load_params(self) -> Optional[LearnedParams]:
        """저장된 학습 파라미터 로드"""
        if not os.path.exists(self.PARAMS_FILE):
            return None
        try:
            with open(self.PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            params = LearnedParams()
            for k, v in data.items():
                if k == "best_rsi_buy_range" or k == "best_rsi_sell_range":
                    setattr(params, k, tuple(v))
                elif hasattr(params, k):
                    setattr(params, k, v)
            return params
        except Exception as e:
            logger.debug("학습 파라미터 로드 실패: %s", e)
            return None

    def _save_params(self, params: LearnedParams):
        """학습 결과를 JSON으로 저장"""
        try:
            data = asdict(params)
            # tuple → list for JSON
            data["best_rsi_buy_range"] = list(params.best_rsi_buy_range)
            data["best_rsi_sell_range"] = list(params.best_rsi_sell_range)
            os.makedirs(os.path.dirname(self.PARAMS_FILE) or ".", exist_ok=True)
            with open(self.PARAMS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("[학습] %d건 분석 결과 저장: %s", params.total_trades, self.PARAMS_FILE)
        except Exception as e:
            logger.debug("학습 파라미터 저장 실패: %s", e)

    def _bucket_win_rate(self, sells: List[Dict], key: str,
                         buckets: list) -> Dict[str, float]:
        """지표를 구간으로 나누어 구간별 승률 계산"""
        result = {}
        for name, lo, hi in buckets:
            trades_in = [t for t in sells
                         if lo <= float(t.get(key, 0) or 0) < hi
                         and float(t.get(key, 0) or 0) > 0]
            if len(trades_in) >= 3:  # 최소 3건 이상이어야 의미 있음
                wins = sum(1 for t in trades_in if float(t.get("pnl_pct", 0)) > 0)
                result[name] = wins / len(trades_in) * 100
        return result

    def _hour_analysis(self, sells: List[Dict]) -> Dict[int, float]:
        """시간대별 승률 계산 (최소 3건 이상)"""
        hour_stats: Dict[int, Dict] = {}
        for t in sells:
            dt_str = t.get("datetime", "")
            if len(dt_str) >= 13:
                try:
                    hour = int(dt_str[11:13])
                    if hour not in hour_stats:
                        hour_stats[hour] = {"wins": 0, "total": 0}
                    hour_stats[hour]["total"] += 1
                    if float(t.get("pnl_pct", 0)) > 0:
                        hour_stats[hour]["wins"] += 1
                except ValueError:
                    pass

        result = {}
        for h, s in hour_stats.items():
            if s["total"] >= 3:  # v2: 2→3 (통계적 최소 표본)
                result[h] = s["wins"] / s["total"] * 100
        return result

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
        params = self.learn_and_save(bot_filter)
        return params.summary()
