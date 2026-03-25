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
    # 대형 주도주
    "반도체": ["005930", "000660", "042700", "058470", "069660", "403870"],
    "2차전지": ["373220", "006400", "051910", "003670", "247540", "064350"],
    "바이오": ["207940", "068270", "145020", "091990", "326030", "328130"],
    # 테마/중소형 (단타 핵심 - 변동성 높음)
    "AI/로봇": ["042660", "336260", "454910", "377300", "099190", "222160"],
    "초전도체": ["017370", "357550", "047810"],
    "양자컴퓨터": ["091990", "046310", "089030"],
    "원전": ["009770", "267260", "092790"],
    "방산": ["012450", "047810", "064350", "141080"],
    "우주항공": ["047810", "299660", "354200"],
    "엔터": ["352820", "041510", "122870", "060260"],
    "게임": ["036570", "263750", "112040", "194480"],
    # 중형 변동주
    "인터넷": ["035420", "035720", "263750"],
    "자동차": ["005380", "000270", "012330"],
    "조선": ["010140", "009540", "329180"],
    "건설": ["000720", "047040", "034220"],
    "화장품": ["090430", "285130", "192820"],
}

# 단타 기준 (스윙보다 공격적) - 장 초반에도 후보 잡히도록 완화
MIN_TRADE_VALUE = 3_000_000_000    # 30억 이상 (장 초반 대응)
MIN_CHANGE_PCT = 1.0               # 1.0% 이상 움직이는 종목
BREAKOUT_LOOKBACK = 10             # 최근 10봉 기준 돌파 (짧게)
VOL_SURGE_RATIO = 1.5              # 전일 대비 150%+ (장 초반 대응)


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

        # 2단계 전부 탈락 시, 1단계 상위 3종목 최소 보장
        if not filtered and candidates:
            logger.warning("[스캐너] 2단계 전부 탈락 → 1단계 상위 3종목 사용")
            candidates.sort(key=lambda c: c.trade_value, reverse=True)
            for c in candidates[:3]:
                if c.score < 10:
                    c.score = 10
                c.reasons.append("2단계면제(최소보장)")
            filtered = candidates[:3]

        scored = self._stage3_sector_scoring(filtered)
        scored.sort(key=lambda r: r.score, reverse=True)

        self._cache = scored
        self._cache_time = now

        if scored:
            logger.info("[스캐너] %d개 종목: %s",
                        len(scored),
                        " / ".join("%s(%+.1f%%,%.0f점)" % (r.name, r.change_pct, r.score)
                                   for r in scored[:5]))
        else:
            logger.warning("[스캐너] 최종 후보 0종목 (1단계 %d종목 모두 탈락)", len(candidates))
        return scored

    def get_best(self) -> Optional[ScanResult]:
        results = self.scan()
        for r in results:
            if r.code not in self._excluded:
                return r
        return None

    # ── 1단계: 거래대금 상위 + 전일 대비 거래량 급증 ──

    def _stage1_volume_scan(self) -> List[ScanResult]:
        if not self.kis.is_authenticated:
            logger.info("[스캐너 1단계] KIS 미인증 — 토큰 대기 중")
            return []

        rankings = self.kis.get_volume_rank(limit=50)

        if not rankings:
            logger.warning("[스캐너 1단계] get_volume_rank 응답 0건 — API 오류 또는 장 미개시")
            return []

        logger.info("[스캐너 1단계] 거래량순위 %d종목 수신", len(rankings))

        # 상위 5개 종목 현황 로깅 (어떤 종목이 오는지 확인용)
        for i, item in enumerate(rankings[:5]):
            tv = item.get("trade_value", 0)
            logger.info(
                "[스캐너 1단계] #%d %s %s | %+.1f%% | 거래대금: %s억 | 거래량: %s",
                i + 1, item.get("code", "?"), item.get("name", "?"),
                item.get("change_pct", 0),
                "{:,.0f}".format(tv / 1_0000_0000) if tv else "0",
                "{:,}".format(item.get("volume", 0)),
            )

        candidates = []
        skip_low_value = 0
        skip_excluded = 0

        for item in rankings:
            code = item["code"]
            if code in self._excluded:
                skip_excluded += 1
                continue

            trade_val = item.get("trade_value", 0)
            change_pct = item.get("change_pct", 0)
            volume = item.get("volume", 0)

            # 기본 필터: 거래대금 30억+ 또는 등락률 1.0%+
            if trade_val < MIN_TRADE_VALUE and change_pct < MIN_CHANGE_PCT:
                skip_low_value += 1
                if skip_low_value <= 3:
                    logger.debug(
                        "[스캐너 1단계] 탈락: %s %s | 거래대금 %s억 < %s억, 등락률 %.1f%% < %.1f%%",
                        code, item.get("name", "?"),
                        "{:,.0f}".format(trade_val / 1_0000_0000),
                        "{:,.0f}".format(MIN_TRADE_VALUE / 1_0000_0000),
                        change_pct, MIN_CHANGE_PCT,
                    )
                continue

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

            # 거래대금 또는 등락률 조건 통과 → 후보에 추가
            candidates.append(cand)

        logger.info("[스캐너 1단계] 후보: %d종목 (제외됨: 거래대금미달 %d, 이미매매 %d)",
                    len(candidates), skip_low_value, skip_excluded)

        # 후보 0이면 거래대금 상위 5개를 무조건 포함 (장 초반 대응)
        if not candidates and rankings:
            logger.warning("[스캐너 1단계] 후보 0 → 거래대금 상위 5종목 강제 편입")
            for item in rankings[:5]:
                code = item["code"]
                if code in self._excluded:
                    continue
                candidates.append(ScanResult(
                    code=code,
                    name=item.get("name", ""),
                    price=item.get("price", 0),
                    change_pct=item.get("change_pct", 0),
                    trade_value=item.get("trade_value", 0),
                    volume=item.get("volume", 0),
                    score=20,
                    reasons=["거래대금상위(강제편입)"],
                ))

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
                logger.debug("[스캐너 2단계] %s %s OHLCV 부족 → 스킵", cand.code, cand.name)
                continue

            df = self.ti.add_all(df)
            latest = df.iloc[-1]

            # 전일 대비 거래량 급증 체크 (1단계에서 이동)
            if len(df) >= 2:
                today_vol = float(df["volume"].iloc[-1])
                prev_vol = float(df["volume"].iloc[-2])
                if prev_vol > 0:
                    vol_surge = today_vol / prev_vol
                    if vol_surge >= VOL_SURGE_RATIO:
                        score += 25
                        reasons.append("거래량전일비%.0f%%" % (vol_surge * 100))

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

            # 거래량 급증 (당일 봉 기준 — 이평 대비)
            vol_ratio = latest.get("vol_ratio")
            if pd.notna(vol_ratio) and vol_ratio > 2.0:
                score += 15
                reasons.append("거래량%.1fx" % vol_ratio)

            # 등락률 가산 (단타는 이미 움직이는 종목이 중요)
            if cand.change_pct >= 10:
                score += 20
                reasons.append("급등%.1f%%" % cand.change_pct)
            elif cand.change_pct >= 5:
                score += 15
                reasons.append("상승%.1f%%" % cand.change_pct)
            elif cand.change_pct >= 3:
                score += 10
                reasons.append("강세%.1f%%" % cand.change_pct)

            # 거래대금 가산 (단타는 유동성이 핵심)
            if cand.trade_value >= 100_000_000_000:
                score += 10
                reasons.append("거래대금%s억" % "{:,.0f}".format(cand.trade_value / 100_000_000))

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

            if score >= 10:
                cand.score = score
                cand.reasons = reasons
                filtered.append(cand)
            else:
                logger.debug("[스캐너 2단계] 탈락: %s %s | 점수 %d < 10", cand.code, cand.name, score)

        logger.info("[스캐너 2단계] 돌파 후보: %d종목 (분석: %d종목)", len(filtered), min(len(candidates), 25))
        return filtered

    # ── 3단계: 섹터 쏠림 + 대장주 + 섹터 평균 등락률 ──

    def _calc_sector_avg_change(self, sector_codes: List[str]) -> float:
        """섹터 내 종목들의 평균 등락률 계산"""
        changes = []
        for code in sector_codes[:5]:
            info = self.kis.get_current_price(code)
            if info and info.get("change_pct") is not None:
                changes.append(info["change_pct"])
        return sum(changes) / len(changes) if changes else 0.0

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

        # 섹터 가산 + 대장주 + 섹터 평균 등락률
        for sector_name, members in sector_hits.items():
            members.sort(key=lambda m: m.change_pct, reverse=True)

            # 섹터 평균 등락률 계산
            sector_codes = SECTOR_MAP.get(sector_name, [])
            sector_avg = self._calc_sector_avg_change(sector_codes)

            for i, cand in enumerate(members):
                cand.sector = sector_name
                hits = len(members)
                cand.score += hits * 10
                cand.reasons.append("섹터쏠림(%s:%d종목)" % (sector_name, hits))

                # 섹터 평균 +3% 이상이면 추가 보너스
                if sector_avg >= 3.0:
                    cand.score += 15
                    cand.reasons.append("섹터강세(평균%+.1f%%)" % sector_avg)
                elif sector_avg >= 1.5:
                    cand.score += 5

                if i == 0:
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
