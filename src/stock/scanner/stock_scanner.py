"""종목 스캐너 - 거래대금 급증 + 돌파 + 섹터 쏠림 감지

3단계 파이프라인:
  1단계: 거래대금 상위 종목 스캐닝 (500억+ 또는 전일 200%+)
  2단계: 당일 신고가 돌파 + 정배열 초입 필터
  3단계: 섹터/테마 동시 급등 감지 → 최적 종목 선택
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
    "반도체": ["005930", "000660", "042700", "058470"],
    "2차전지": ["373220", "006400", "051910", "003670"],
    "바이오": ["207940", "068270", "145020", "091990"],
    "인터넷/플랫폼": ["035420", "035720", "263750", "036570"],
    "자동차": ["005380", "000270", "012330"],
    "금융": ["105560", "055550", "086790", "316140"],
    "엔터": ["352820", "041510", "122870"],
    "방산": ["012450", "047810", "064350"],
}

MIN_TRADE_VALUE = 50_000_000_000   # 최소 거래대금 500억
MIN_CHANGE_PCT = 2.0               # 최소 등락률 2%
BREAKOUT_LOOKBACK = 20             # 돌파 판단 기간 (봉 수)


@dataclass
class ScanResult:
    """스캔된 종목 정보"""
    code: str
    name: str
    price: int
    change_pct: float
    trade_value: int
    volume: int
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    sector: str = ""

    def summary(self) -> str:
        reasons_str = ", ".join(self.reasons) if self.reasons else "기본"
        return "%s %s | %s원 (%+.1f%%) | 거래대금: %s억 | 점수: %.0f | %s%s" % (
            self.code, self.name,
            "{:,}".format(self.price), self.change_pct,
            "{:,.0f}".format(self.trade_value / 100_000_000),
            self.score, reasons_str,
            " [%s]" % self.sector if self.sector else "",
        )


class StockScanner:
    """실시간 종목 스캐너"""

    def __init__(self, kis: KISClient):
        self.kis = kis
        self.ti = TechnicalIndicators(ma_short=5, ma_long=20)
        self._cache: List[ScanResult] = []
        self._cache_time: float = 0
        self._cache_ttl = 30
        self._excluded: Set[str] = set()

    def exclude(self, code: str):
        """특정 종목 스캔 대상에서 제외"""
        self._excluded.add(code)

    def clear_exclusions(self):
        self._excluded.clear()

    def scan(self, force: bool = False) -> List[ScanResult]:
        """3단계 파이프라인 실행하여 매수 후보 반환"""
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        # 1단계: 거래대금 상위 종목 수집
        candidates = self._stage1_volume_scan()
        if not candidates:
            self._cache = []
            self._cache_time = now
            return []

        # 2단계: 돌파 + 정배열 필터
        filtered = self._stage2_breakout_filter(candidates)

        # 3단계: 섹터 쏠림 보너스
        scored = self._stage3_sector_scoring(filtered)

        scored.sort(key=lambda r: r.score, reverse=True)
        self._cache = scored
        self._cache_time = now

        if scored:
            logger.info("[스캐너] %d개 종목 발견: %s",
                        len(scored),
                        " / ".join("%s(%+.1f%%)" % (r.name, r.change_pct) for r in scored[:5]))

        return scored

    def get_best(self) -> Optional[ScanResult]:
        """최고 점수 종목 1개 반환"""
        results = self.scan()
        for r in results:
            if r.code not in self._excluded:
                return r
        return None

    # ── 1단계: 거래대금 상위 스캐닝 ──

    def _stage1_volume_scan(self) -> List[ScanResult]:
        """거래대금 500억+ 또는 전일 대비 급증 종목 수집"""
        rankings = self.kis.get_volume_rank(limit=40)

        candidates = []
        for item in rankings:
            code = item["code"]
            if code in self._excluded:
                continue
            trade_val = item.get("trade_value", 0)
            change_pct = item.get("change_pct", 0)

            if trade_val < MIN_TRADE_VALUE and change_pct < MIN_CHANGE_PCT:
                continue

            candidates.append(ScanResult(
                code=code,
                name=item.get("name", ""),
                price=item.get("price", 0),
                change_pct=change_pct,
                trade_value=trade_val,
                volume=item.get("volume", 0),
                score=0,
                reasons=[],
            ))

        logger.debug("[스캐너 1단계] 거래대금 후보: %d종목", len(candidates))
        return candidates

    # ── 2단계: 돌파 + 정배열 필터 ──

    def _stage2_breakout_filter(self, candidates: List[ScanResult]) -> List[ScanResult]:
        """당일 신고가 돌파 + 이평선 정배열 초입 종목 필터"""
        filtered = []

        for cand in candidates[:20]:
            score = 0.0
            reasons = []

            df = self.kis.get_ohlcv(cand.code, period="D", count=60)
            if df is None or len(df) < BREAKOUT_LOOKBACK:
                continue

            df = self.ti.add_all(df)
            latest = df.iloc[-1]

            # 당일 신고가 돌파 체크
            recent_high = df["high"].iloc[-BREAKOUT_LOOKBACK:-1].max()
            if latest["close"] > recent_high:
                score += 30
                reasons.append("신고가돌파")

            # 이평선 정배열 확인 (5일 > 20일)
            ma5 = latest.get("ma_short")
            ma20 = latest.get("ma_long")
            if pd.notna(ma5) and pd.notna(ma20) and ma5 > ma20:
                score += 20
                reasons.append("정배열")

                prev = df.iloc[-3] if len(df) >= 3 else None
                if prev is not None:
                    prev_ma5 = prev.get("ma_short")
                    prev_ma20 = prev.get("ma_long")
                    if pd.notna(prev_ma5) and pd.notna(prev_ma20):
                        if prev_ma5 <= prev_ma20 and ma5 > ma20:
                            score += 15
                            reasons.append("정배열전환")

            # RSI 적정 구간 (40~65: 과매수 아니면서 상승 여력)
            rsi = latest.get("rsi")
            if pd.notna(rsi) and 40 <= rsi <= 65:
                score += 10
                reasons.append("RSI적정(%.0f)" % rsi)

            # 거래량 급증
            vol_ratio = latest.get("vol_ratio")
            if pd.notna(vol_ratio) and vol_ratio > 2.0:
                score += 15
                reasons.append("거래량%.1fx" % vol_ratio)

            # 등락률 가산
            if cand.change_pct > 5:
                score += 10
                reasons.append("급등%.1f%%" % cand.change_pct)

            if score >= 20:
                cand.score = score
                cand.reasons = reasons
                filtered.append(cand)

        logger.debug("[스캐너 2단계] 돌파 후보: %d종목", len(filtered))
        return filtered

    # ── 3단계: 섹터 쏠림 감지 ──

    def _stage3_sector_scoring(self, candidates: List[ScanResult]) -> List[ScanResult]:
        """같은 섹터 종목이 동시에 급등하면 보너스 점수"""
        code_set = {c.code for c in candidates}

        sector_hits: Dict[str, int] = {}
        code_to_sector: Dict[str, str] = {}

        for sector_name, codes in SECTOR_MAP.items():
            hits = sum(1 for c in codes if c in code_set)
            if hits >= 2:
                sector_hits[sector_name] = hits
                for c in codes:
                    code_to_sector[c] = sector_name

        if not sector_hits:
            up_stocks = self.kis.get_price_change_rank("up", 20)
            up_codes = {s["code"] for s in up_stocks}
            for sector_name, codes in SECTOR_MAP.items():
                hits = sum(1 for c in codes if c in up_codes)
                if hits >= 2:
                    sector_hits[sector_name] = hits
                    for c in codes:
                        code_to_sector[c] = sector_name

        for cand in candidates:
            sector = code_to_sector.get(cand.code, "")
            if sector:
                cand.sector = sector
                hits = sector_hits.get(sector, 0)
                bonus = hits * 10
                cand.score += bonus
                cand.reasons.append("섹터쏠림(%s:%d종목)" % (sector, hits))

        if sector_hits:
            logger.info("[스캐너 3단계] 섹터 쏠림 감지: %s",
                        ", ".join("%s(%d종목)" % (k, v) for k, v in sector_hits.items()))

        return candidates
