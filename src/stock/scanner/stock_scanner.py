"""종목 스캐너 v2 - 거래량 급증 + 돌파 + 섹터 대장주 + 호가창

3단계 파이프라인:
  1단계: 거래대금 상위 + 전일 동시간 대비 거래량 300%+ 급증 종목
  2단계: 당일 신고가 돌파 + 정배열 초입 + 호가창 건전성
  3단계: 섹터 쏠림 감지 → 대장주(상승률 1위) 우선 선택
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import pandas as pd

from src.stock.kis_client import KISClient
from src.indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)

SECTOR_MAP = {
    "반도체": ["005930", "000660", "042700", "058470", "069660"],
    "2차전지": ["373220", "006400", "051910", "003670", "247540"],
    "바이오": ["207940", "068270", "145020", "091990", "326030"],
    "인터넷/플랫폼": ["035420", "035720", "263750", "036570"],
    "자동차": ["005380", "000270", "012330", "316140"],
    "금융": ["105560", "055550", "086790", "316140"],
    "엔터": ["352820", "041510", "122870"],
    "방산": ["012450", "047810", "064350"],
    "AI/로봇": ["042660", "336260", "454910", "377300"],
    "조선": ["010140", "009540", "042660"],
}

MIN_TRADE_VALUE = 50_000_000_000
MIN_CHANGE_PCT = 2.0
BREAKOUT_LOOKBACK = 20
VOL_SURGE_RATIO = 3.0          # 전일 대비 거래량 300%+


@dataclass
class ScanResult:
    code: str
    name: str
    price: int
    change_pct: float
    trade_value: int
    volume: int
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    sector: str = ""
    is_sector_leader: bool = False

    def summary(self) -> str:
        reasons_str = ", ".join(self.reasons) if self.reasons else "기본"
        leader = " ★대장주" if self.is_sector_leader else ""
        return "%s %s | %s원 (%+.1f%%) | 거래대금: %s억 | 점수: %.0f | %s%s%s" % (
            self.code, self.name,
            "{:,}".format(self.price), self.change_pct,
            "{:,.0f}".format(self.trade_value / 100_000_000),
            self.score, reasons_str,
            " [%s]" % self.sector if self.sector else "",
            leader,
        )


class StockScanner:

    def __init__(self, kis: KISClient):
        self.kis = kis
        self.ti = TechnicalIndicators(ma_short=5, ma_long=20)
        self._cache: List[ScanResult] = []
        self._cache_time: float = 0
        self._cache_ttl = 30
        self._excluded: Set[str] = set()

    def exclude(self, code: str):
        self._excluded.add(code)

    def clear_exclusions(self):
        self._excluded.clear()

    def scan(self, force: bool = False) -> List[ScanResult]:
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        candidates = self._stage1_volume_scan()
        if not candidates:
            self._cache = []
            self._cache_time = now
            return []

        filtered = self._stage2_breakout_filter(candidates)
        scored = self._stage3_sector_scoring(filtered)
        scored.sort(key=lambda r: r.score, reverse=True)

        self._cache = scored
        self._cache_time = now

        if scored:
            logger.info("[스캐너] %d개 종목: %s",
                        len(scored),
                        " / ".join("%s(%+.1f%%,%.0f점)" % (r.name, r.change_pct, r.score)
                                   for r in scored[:5]))
        return scored

    def get_best(self) -> Optional[ScanResult]:
        results = self.scan()
        for r in results:
            if r.code not in self._excluded:
                return r
        return None

    # ── 1단계: 거래대금 상위 + 전일 대비 거래량 급증 ──

    def _stage1_volume_scan(self) -> List[ScanResult]:
        rankings = self.kis.get_volume_rank(limit=40)

        candidates = []
        for item in rankings:
            code = item["code"]
            if code in self._excluded:
                continue

            trade_val = item.get("trade_value", 0)
            change_pct = item.get("change_pct", 0)
            volume = item.get("volume", 0)

            # 기본 필터: 거래대금 500억+ 또는 등락률 2%+
            if trade_val < MIN_TRADE_VALUE and change_pct < MIN_CHANGE_PCT:
                continue

            vol_surge = self._check_volume_surge(code)

            cand = ScanResult(
                code=code,
                name=item.get("name", ""),
                price=item.get("price", 0),
                change_pct=change_pct,
                trade_value=trade_val,
                volume=volume,
                score=0,
                reasons=[],
            )

            # 전일 대비 거래량 급증이면 별도 가산 + 무조건 포함
            if vol_surge >= VOL_SURGE_RATIO:
                cand.score += 25
                cand.reasons.append("거래량전일비%.0f%%" % (vol_surge * 100))
                candidates.append(cand)
            elif trade_val >= MIN_TRADE_VALUE or change_pct >= MIN_CHANGE_PCT:
                candidates.append(cand)

        logger.debug("[스캐너 1단계] 후보: %d종목", len(candidates))
        return candidates

    def _check_volume_surge(self, code: str) -> float:
        """전일 동시간 대비 거래량 비율. 예: 3.5 = 350%"""
        try:
            df = self.kis.get_ohlcv(code, period="D", count=3)
            if df is None or len(df) < 2:
                return 0.0
            today_vol = float(df["volume"].iloc[-1])
            prev_vol = float(df["volume"].iloc[-2])
            if prev_vol <= 0:
                return 10.0 if today_vol > 0 else 0.0
            return today_vol / prev_vol
        except Exception:
            return 0.0

    # ── 2단계: 돌파 + 정배열 + 호가창 ──

    def _stage2_breakout_filter(self, candidates: List[ScanResult]) -> List[ScanResult]:
        filtered = []

        for cand in candidates[:25]:
            score = cand.score
            reasons = list(cand.reasons)

            df = self.kis.get_ohlcv(cand.code, period="D", count=60)
            if df is None or len(df) < BREAKOUT_LOOKBACK:
                continue

            df = self.ti.add_all(df)
            latest = df.iloc[-1]

            # 당일 신고가 돌파
            recent_high = df["high"].iloc[-BREAKOUT_LOOKBACK:-1].max()
            if latest["close"] > recent_high:
                score += 30
                reasons.append("신고가돌파")

            # 이평선 정배열
            ma5 = latest.get("ma_short")
            ma20 = latest.get("ma_long")
            if pd.notna(ma5) and pd.notna(ma20) and ma5 > ma20:
                score += 20
                reasons.append("정배열")

                # 정배열 막 전환
                if len(df) >= 3:
                    prev = df.iloc[-3]
                    p5 = prev.get("ma_short")
                    p20 = prev.get("ma_long")
                    if pd.notna(p5) and pd.notna(p20) and p5 <= p20:
                        score += 15
                        reasons.append("정배열전환")

            # RSI 적정 (40~65)
            rsi = latest.get("rsi")
            if pd.notna(rsi) and 40 <= rsi <= 65:
                score += 10
                reasons.append("RSI적정(%.0f)" % rsi)

            # 거래량 급증 (당일 봉 기준)
            vol_ratio = latest.get("vol_ratio")
            if pd.notna(vol_ratio) and vol_ratio > 2.0:
                score += 15
                reasons.append("거래량%.1fx" % vol_ratio)

            # 등락률 가산
            if cand.change_pct > 5:
                score += 10
                reasons.append("급등%.1f%%" % cand.change_pct)

            # 호가창 건전성 체크
            ob = self.kis.get_orderbook_ratio(cand.code)
            if ob:
                ratio = ob["bid_ask_ratio"]
                total_ask = ob["total_ask"]
                total_bid = ob["total_bid"]

                if 0.5 <= ratio <= 2.0 and total_ask > 0:
                    score += 10
                    reasons.append("호가건전(비율:%.1f)" % ratio)
                elif ratio > 3.0:
                    # 매수 잔량만 과다 = 개미 받침 = 위험
                    score -= 15
                    reasons.append("호가위험(매수과다:%.1f)" % ratio)
                elif ratio < 0.3:
                    # 매도 잔량 압도적 = 매도세 강함
                    score -= 10
                    reasons.append("호가위험(매도압도)")

            if score >= 20:
                cand.score = score
                cand.reasons = reasons
                filtered.append(cand)

        logger.debug("[스캐너 2단계] 돌파 후보: %d종목", len(filtered))
        return filtered

    # ── 3단계: 섹터 쏠림 + 대장주 판별 ──

    def _stage3_sector_scoring(self, candidates: List[ScanResult]) -> List[ScanResult]:
        code_set = {c.code for c in candidates}
        code_to_cand = {c.code: c for c in candidates}

        sector_hits: Dict[str, List[ScanResult]] = {}

        for sector_name, codes in SECTOR_MAP.items():
            members = [code_to_cand[c] for c in codes if c in code_set]
            if len(members) >= 2:
                sector_hits[sector_name] = members

        # 등락률 상위로 보충
        if not sector_hits:
            up_stocks = self.kis.get_price_change_rank("up", 20)
            up_codes = {s["code"] for s in up_stocks}
            for sector_name, codes in SECTOR_MAP.items():
                overlap = [c for c in codes if c in up_codes]
                overlap_cands = [code_to_cand[c] for c in overlap if c in code_set]
                if len(overlap) >= 2 and overlap_cands:
                    sector_hits[sector_name] = overlap_cands

        # 섹터 가산 + 대장주 판별
        for sector_name, members in sector_hits.items():
            members.sort(key=lambda m: m.change_pct, reverse=True)
            leader = members[0]

            for i, cand in enumerate(members):
                cand.sector = sector_name
                hits = len(members)
                cand.score += hits * 10
                cand.reasons.append("섹터쏠림(%s:%d종목)" % (sector_name, hits))

                if i == 0:
                    # 대장주 (상승률 1위)
                    cand.score += 20
                    cand.is_sector_leader = True
                    cand.reasons.append("★대장주(+%.1f%%)" % cand.change_pct)

        # 섹터 안 속한 후보에도 섹터 체크
        for cand in candidates:
            if cand.sector:
                continue
            for sector_name, codes in SECTOR_MAP.items():
                if cand.code in codes:
                    cand.sector = sector_name
                    break

        if sector_hits:
            logger.info("[스캐너 3단계] 섹터: %s",
                        ", ".join("%s(%d종목, 대장:%s)" % (
                            k, len(v), v[0].name if v else "?")
                                  for k, v in sector_hits.items()))

        return candidates
