"""멀티소스 종목 발굴기 — KIS API 기반

거래량 스캐너와 별도로, 추가 소스에서 종목을 발굴한다.

소스:
  1. 낙폭과대 반등 후보 (등락률 하위) — 급락 후 반등 기대
  2. 거래량 상위 종목에서 수급 체크 — 외인/기관 동반매수

KIS API만 사용하므로 장중 실시간 작동.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_PRICE = 2000

ETF_KEYWORDS = (
    "KODEX", "KOSEF", "TIGER", "KBSTAR", "HANARO",
    "SOL", "ACE", "RISE", "PLUS", "인버스", "레버리지",
    "ETN", "선물", "채권", "리츠", "스팩",
)


@dataclass
class SourceResult:
    """소스별 발굴 결과"""
    code: str
    name: str
    price: int = 0
    change_pct: float = 0.0
    trade_value: int = 0
    volume: int = 0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    source: str = ""


class MultiSourceScanner:
    """KIS API 기반 멀티소스 종목 발굴기"""

    def __init__(self, kis_client=None):
        self.kis = kis_client
        self._cache: Dict[str, List[SourceResult]] = {}
        self._cache_time: float = 0
        self._cache_ttl = 1800  # 30분 캐시

    def scan_all(self, force: bool = False) -> Dict[str, List[SourceResult]]:
        """모든 소스에서 스캔."""
        if self.kis is None:
            logger.warning("[멀티소스] KIS 클라이언트 없음")
            return {"oversold": [], "flow": []}

        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        results = {}
        results["oversold"] = self._scan_oversold()
        results["flow"] = self._scan_strong_flow()

        self._cache = results
        self._cache_time = now

        total = sum(len(v) for v in results.values())
        logger.info("[멀티소스] 스캔 완료: 낙폭과대 %d, 수급강세 %d (총 %d)",
                    len(results["oversold"]), len(results["flow"]), total)
        return results

    def get_merged_candidates(self, limit: int = 15) -> List[SourceResult]:
        """모든 소스 결과를 중복 제거 + 합산 점수로 정렬."""
        sources = self.scan_all()
        merged: Dict[str, SourceResult] = {}

        for source_name, items in sources.items():
            for item in items:
                if item.code in merged:
                    existing = merged[item.code]
                    existing.score += item.score
                    existing.reasons.extend(item.reasons)
                    existing.source += "+" + item.source
                else:
                    merged[item.code] = SourceResult(
                        code=item.code, name=item.name,
                        price=item.price, change_pct=item.change_pct,
                        trade_value=item.trade_value, volume=item.volume,
                        score=item.score, reasons=list(item.reasons),
                        source=item.source,
                    )

        for item in merged.values():
            source_count = len(item.source.split("+"))
            if source_count >= 2:
                item.score += 15
                item.reasons.append("멀티소스%d개" % source_count)

        result = sorted(merged.values(), key=lambda x: x.score, reverse=True)

        if result:
            logger.info("[멀티소스] 병합 %d종목: %s",
                        len(result),
                        " / ".join("%s(%.0f점)" % (r.name, r.score) for r in result[:5]))
        return result[:limit]

    # ── 소스1: 낙폭과대 반등 후보 ──

    def _scan_oversold(self) -> List[SourceResult]:
        """등락률 하위 종목 중 반등 가능성 있는 종목"""
        try:
            rankings = self.kis.get_price_change_rank(direction="down", limit=30)
        except Exception as e:
            logger.debug("[낙폭과대] 등락률 조회 실패: %s", e)
            return []

        if not rankings:
            return []

        results = []
        for item in rankings:
            name = item.get("name", "")
            code = item.get("code", "")
            price = item.get("price", 0)
            change = item.get("change_pct", 0)
            trade_val = item.get("trade_value", 0)
            volume = item.get("volume", 0)

            if not code or price < MIN_PRICE:
                continue
            if any(kw in name for kw in ETF_KEYWORDS):
                continue
            # -3% ~ -15% 범위만 (너무 심한 급락은 이유가 있을 수 있음)
            if change > -3 or change < -15:
                continue
            # 거래대금 100억 이상
            if trade_val < 10_000_000_000:
                continue

            score = 20
            reasons = ["낙폭%.1f%%" % change]

            if change <= -10:
                score += 20
            elif change <= -7:
                score += 15
            elif change <= -5:
                score += 10
            else:
                score += 5

            if trade_val >= 50_000_000_000:
                score += 10
                reasons.append("거래대금%s억" % "{:,.0f}".format(trade_val / 100_000_000))

            results.append(SourceResult(
                code=code, name=name, price=price,
                change_pct=change, trade_value=trade_val,
                volume=volume, score=score,
                reasons=reasons, source="oversold",
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        logger.info("[낙폭과대] %d종목 발굴", len(results))
        return results[:10]

    # ── 소스2: 수급 강세 종목 ──

    def _scan_strong_flow(self) -> List[SourceResult]:
        """거래량 상위 종목 중 외인+기관 매수세가 강한 종목"""
        try:
            rankings = self.kis.get_volume_rank(market="J", limit=30)
        except Exception as e:
            logger.debug("[수급강세] 거래량순위 조회 실패: %s", e)
            return []

        if not rankings:
            return []

        results = []
        checked = 0
        for item in rankings:
            name = item.get("name", "")
            code = item.get("code", "")
            price = item.get("price", 0)
            change = item.get("change_pct", 0)
            trade_val = item.get("trade_value", 0)
            volume = item.get("volume", 0)

            if not code or price < MIN_PRICE:
                continue
            if any(kw in name for kw in ETF_KEYWORDS):
                continue
            # 급등주/급락주 제외
            if change >= 10 or change <= -5:
                continue

            # API 호출 제한: 최대 15종목만 수급 체크
            if checked >= 15:
                break
            checked += 1

            # 투자자별 매매동향 조회
            try:
                trend = self.kis.get_investor_trend(code)
            except Exception:
                continue
            if not trend:
                continue

            foreign = trend.get("foreign_net", 0)
            inst = trend.get("institution_net", 0)

            # 외인 또는 기관이 순매수여야 함
            if foreign <= 0 and inst <= 0:
                continue

            score = 15
            reasons = []

            if foreign > 0 and inst > 0:
                score += 25
                reasons.append("외인+기관동반매수")
            elif foreign > 0:
                score += 15
                reasons.append("외인순매수")
            elif inst > 0:
                score += 10
                reasons.append("기관순매수")

            if trade_val >= 50_000_000_000:
                score += 10
                reasons.append("거래대금%s억" % "{:,.0f}".format(trade_val / 100_000_000))

            if 2 <= change <= 5:
                score += 5
                reasons.append("상승%+.1f%%" % change)

            results.append(SourceResult(
                code=code, name=name, price=price,
                change_pct=change, trade_value=trade_val,
                volume=volume, score=score,
                reasons=reasons, source="flow",
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        logger.info("[수급강세] %d종목 발굴", len(results))
        return results[:10]
