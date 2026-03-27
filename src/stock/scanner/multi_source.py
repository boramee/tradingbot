"""멀티소스 종목 발굴기 — pykrx 기반

4가지 소스에서 종목을 발굴하고, 중복 제거 + 합산 점수로 최종 후보를 만든다.

소스:
  1. 외국인 순매수 상위 (5일 누적) — 스윙 핵심
  2. 기관 순매수 상위 (5일 누적) — 수급 보강
  3. 52주 신고가 근접 종목 — 돌파 후 눌림목
  4. 낙폭과대 반등 후보 — 급락 후 반등 기대

각 소스 결과를 ScanResult로 통일해서 기존 스캐너와 합산 가능.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 공통 필터
MIN_PRICE = 2000           # 2천원 미만 제외 (잡주)
MIN_MARKET_CAP = 300_000_000_000  # 시가총액 3000억 이상

ETF_KEYWORDS = (
    "KODEX", "KOSEF", "TIGER", "KBSTAR", "HANARO",
    "SOL", "ACE", "RISE", "PLUS", "인버스", "레버리지",
    "ETN", "선물", "채권", "리츠",
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
    source: str = ""          # foreign/inst/high52/oversold

    def summary(self) -> str:
        return "%s %s | %s원 (%+.1f%%) | %.0f점 | %s | [%s]" % (
            self.code, self.name,
            "{:,}".format(self.price), self.change_pct,
            self.score, ", ".join(self.reasons), self.source,
        )


class MultiSourceScanner:
    """pykrx 기반 멀티소스 종목 발굴기"""

    def __init__(self):
        self._cache: Dict[str, List[SourceResult]] = {}
        self._cache_time: float = 0
        self._cache_ttl = 1800  # 30분 캐시 (pykrx는 일별 데이터라 자주 안 바뀜)

    def scan_all(self, force: bool = False) -> Dict[str, List[SourceResult]]:
        """모든 소스에서 스캔. source_name → [SourceResult] 딕셔너리 반환."""
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        results = {}
        results["foreign"] = self._scan_foreign_buying()
        results["inst"] = self._scan_inst_buying()
        results["high52"] = self._scan_52week_high()
        results["oversold"] = self._scan_oversold_bounce()

        self._cache = results
        self._cache_time = now

        total = sum(len(v) for v in results.values())
        logger.info("[멀티소스] 스캔 완료: 외인 %d, 기관 %d, 52주신고가 %d, 낙폭과대 %d → 총 %d종목",
                    len(results["foreign"]), len(results["inst"]),
                    len(results["high52"]), len(results["oversold"]), total)
        return results

    def get_merged_candidates(self, limit: int = 15) -> List[SourceResult]:
        """모든 소스 결과를 중복 제거 + 합산 점수로 정렬."""
        sources = self.scan_all()
        merged: Dict[str, SourceResult] = {}

        for source_name, items in sources.items():
            for item in items:
                if item.code in merged:
                    # 중복: 점수 합산 + 소스 태그 추가
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

        # 멀티소스 보너스: 2개 이상 소스에서 발굴되면 추가 점수
        for item in merged.values():
            source_count = len(item.source.split("+"))
            if source_count >= 3:
                item.score += 30
                item.reasons.append("멀티소스%d개" % source_count)
            elif source_count >= 2:
                item.score += 15
                item.reasons.append("멀티소스%d개" % source_count)

        result = sorted(merged.values(), key=lambda x: x.score, reverse=True)

        if result:
            logger.info("[멀티소스] 병합 후 %d종목, 상위: %s",
                        len(result),
                        " / ".join("%s(%.0f점,%s)" % (r.name, r.score, r.source)
                                   for r in result[:5]))
        return result[:limit]

    # ── 소스1: 외국인 순매수 상위 ──

    def _scan_foreign_buying(self) -> List[SourceResult]:
        """최근 5일 외국인 순매수 금액 상위 종목"""
        try:
            from pykrx import stock as pykrx_stock

            end = date.today()
            start = end - timedelta(days=10)
            end_str = end.strftime("%Y%m%d")
            start_str = start.strftime("%Y%m%d")

            results = []
            for market in ("KOSPI", "KOSDAQ"):
                df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
                    start_str, end_str, market, "외국인")
                if df is None or df.empty:
                    continue

                # 순매수거래대금 기준 상위
                col = "순매수거래대금"
                if col not in df.columns:
                    logger.warning("[외국인순매수] 컬럼 없음: %s", df.columns.tolist())
                    continue

                top = df.nlargest(20, col)
                for code, row in top.iterrows():
                    name = str(row.get("종목명", ""))
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue
                    net_amount = int(row[col])
                    if net_amount <= 0:
                        continue

                    # 현재가 조회 (pykrx 당일 OHLCV)
                    price, change_pct, trade_val = self._get_today_price(code)
                    if price < MIN_PRICE:
                        continue

                    score = 30  # 외국인 순매수 기본 점수
                    reasons = []

                    # 순매수 금액 크기별 가산
                    net_billion = net_amount / 100_000_000
                    if net_billion >= 500:
                        score += 25
                        reasons.append("외인순매수%s억" % "{:,.0f}".format(net_billion))
                    elif net_billion >= 100:
                        score += 15
                        reasons.append("외인순매수%s억" % "{:,.0f}".format(net_billion))
                    else:
                        score += 5
                        reasons.append("외인순매수%s억" % "{:,.0f}".format(net_billion))

                    results.append(SourceResult(
                        code=code, name=name, price=price,
                        change_pct=change_pct, trade_value=trade_val,
                        score=score, reasons=reasons, source="foreign",
                    ))

            logger.info("[외국인순매수] %d종목 발굴", len(results))
            return results[:15]

        except Exception as e:
            logger.warning("[외국인순매수] 스캔 실패: %s", e)
            return []

    # ── 소스2: 기관 순매수 상위 ──

    def _scan_inst_buying(self) -> List[SourceResult]:
        """최근 5일 기관 순매수 금액 상위 종목"""
        try:
            from pykrx import stock as pykrx_stock

            end = date.today()
            start = end - timedelta(days=10)
            end_str = end.strftime("%Y%m%d")
            start_str = start.strftime("%Y%m%d")

            results = []
            for market in ("KOSPI", "KOSDAQ"):
                df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
                    start_str, end_str, market, "기관합계")
                if df is None or df.empty:
                    continue

                col = "순매수거래대금"
                if col not in df.columns:
                    continue

                top = df.nlargest(15, col)
                for code, row in top.iterrows():
                    name = str(row.get("종목명", ""))
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue
                    net_amount = int(row[col])
                    if net_amount <= 0:
                        continue

                    price, change_pct, trade_val = self._get_today_price(code)
                    if price < MIN_PRICE:
                        continue

                    score = 20  # 기관 기본 점수 (외국인보다 낮음)
                    net_billion = net_amount / 100_000_000
                    reasons = []
                    if net_billion >= 300:
                        score += 20
                        reasons.append("기관순매수%s억" % "{:,.0f}".format(net_billion))
                    elif net_billion >= 50:
                        score += 10
                        reasons.append("기관순매수%s억" % "{:,.0f}".format(net_billion))
                    else:
                        reasons.append("기관순매수%s억" % "{:,.0f}".format(net_billion))

                    results.append(SourceResult(
                        code=code, name=name, price=price,
                        change_pct=change_pct, trade_value=trade_val,
                        score=score, reasons=reasons, source="inst",
                    ))

            logger.info("[기관순매수] %d종목 발굴", len(results))
            return results[:15]

        except Exception as e:
            logger.warning("[기관순매수] 스캔 실패: %s", e)
            return []

    # ── 소스3: 52주 신고가 근접 ──

    def _scan_52week_high(self) -> List[SourceResult]:
        """52주 신고가 대비 95% 이상인 종목 (돌파 임박 또는 첫 조정)"""
        try:
            from pykrx import stock as pykrx_stock

            today = date.today()
            today_str = today.strftime("%Y%m%d")
            year_ago = (today - timedelta(days=365)).strftime("%Y%m%d")

            results = []
            for market in ("KOSPI", "KOSDAQ"):
                # 오늘 전종목 시세
                ohlcv = pykrx_stock.get_market_ohlcv_by_ticker(today_str, market, alternative=True)
                if ohlcv is None or ohlcv.empty:
                    continue

                # 시가총액으로 대형주만 (API 부하 줄이기)
                cap_df = pykrx_stock.get_market_cap_by_ticker(today_str, market, alternative=True)

                for code in ohlcv.index:
                    row = ohlcv.loc[code]
                    close = int(row.get("종가", 0))
                    if close < MIN_PRICE:
                        continue
                    change_pct = float(row.get("등락률", 0))
                    volume = int(row.get("거래량", 0))
                    trade_val = int(row.get("거래대금", 0))

                    # 시가총액 3000억 미만 제외
                    if cap_df is not None and code in cap_df.index:
                        cap = int(cap_df.loc[code].get("시가총액", 0))
                        if cap < MIN_MARKET_CAP:
                            continue

                    name = pykrx_stock.get_market_ticker_name(code)
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue

                    # 52주 최고가 조회
                    hist = pykrx_stock.get_market_ohlcv(year_ago, today_str, code)
                    if hist is None or len(hist) < 20:
                        continue
                    high_52w = int(hist["고가"].max())
                    if high_52w <= 0:
                        continue

                    ratio = close / high_52w
                    # 52주 신고가 대비 95~102% (신고가 근접 또는 갱신 직후)
                    if ratio < 0.95 or ratio > 1.05:
                        continue

                    score = 25
                    reasons = []
                    if ratio >= 1.0:
                        score += 20
                        reasons.append("52주신고가갱신")
                    elif ratio >= 0.97:
                        score += 10
                        reasons.append("52주고가97%%(%s원)" % "{:,}".format(high_52w))
                    else:
                        reasons.append("52주고가95%%(%s원)" % "{:,}".format(high_52w))

                    # 거래량 동반 시 가산
                    if volume > 0 and trade_val >= 10_000_000_000:
                        score += 10
                        reasons.append("거래대금%s억" % "{:,.0f}".format(trade_val / 100_000_000))

                    results.append(SourceResult(
                        code=code, name=name, price=close,
                        change_pct=change_pct, trade_value=trade_val,
                        volume=volume, score=score,
                        reasons=reasons, source="high52",
                    ))

            results.sort(key=lambda x: x.score, reverse=True)
            logger.info("[52주신고가] %d종목 발굴", len(results))
            return results[:15]

        except Exception as e:
            logger.warning("[52주신고가] 스캔 실패: %s", e)
            return []

    # ── 소스4: 낙폭과대 반등 후보 ──

    def _scan_oversold_bounce(self) -> List[SourceResult]:
        """최근 5일 급락(-10%~-3%) + 시가총액 상위 → 반등 기대"""
        try:
            from pykrx import stock as pykrx_stock

            today = date.today()
            today_str = today.strftime("%Y%m%d")
            five_ago = (today - timedelta(days=8)).strftime("%Y%m%d")

            results = []
            for market in ("KOSPI", "KOSDAQ"):
                df = pykrx_stock.get_market_price_change_by_ticker(
                    five_ago, today_str, market)
                if df is None or df.empty:
                    continue

                cap_df = pykrx_stock.get_market_cap_by_ticker(today_str, market, alternative=True)

                for code in df.index:
                    row = df.loc[code]
                    change = float(row.get("등락률", 0))
                    close = int(row.get("종가", 0))

                    if close < MIN_PRICE:
                        continue
                    # 낙폭과대: 5일간 -3% ~ -15%
                    if change > -3 or change < -15:
                        continue

                    # 시가총액 5000억 이상만 (우량주 급락만)
                    if cap_df is not None and code in cap_df.index:
                        cap = int(cap_df.loc[code].get("시가총액", 0))
                        if cap < 500_000_000_000:
                            continue

                    name = pykrx_stock.get_market_ticker_name(code)
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue

                    trade_val = int(row.get("거래대금", 0))
                    volume = int(row.get("거래량", 0))

                    score = 20
                    reasons = ["5일낙폭%.1f%%" % change]

                    # 낙폭이 클수록 점수 높음 (반등 폭 기대)
                    if change <= -10:
                        score += 15
                    elif change <= -7:
                        score += 10
                    elif change <= -5:
                        score += 5

                    # 시가총액 1조 이상이면 추가 (대형주 급락 = 반등 확률 높음)
                    if cap_df is not None and code in cap_df.index:
                        cap = int(cap_df.loc[code].get("시가총액", 0))
                        if cap >= 1_000_000_000_000:
                            score += 10
                            reasons.append("시총%s조" % "{:,.1f}".format(cap / 1_000_000_000_000))

                    results.append(SourceResult(
                        code=code, name=name, price=close,
                        change_pct=change, trade_value=trade_val,
                        volume=volume, score=score,
                        reasons=reasons, source="oversold",
                    ))

            results.sort(key=lambda x: x.score, reverse=True)
            logger.info("[낙폭과대] %d종목 발굴", len(results))
            return results[:15]

        except Exception as e:
            logger.warning("[낙폭과대] 스캔 실패: %s", e)
            return []

    # ── 유틸 ──

    @staticmethod
    def _get_today_price(code: str) -> tuple:
        """pykrx로 당일 시세 조회. (종가, 등락률, 거래대금) 반환."""
        try:
            from pykrx import stock as pykrx_stock
            today_str = date.today().strftime("%Y%m%d")
            df = pykrx_stock.get_market_ohlcv(today_str, today_str, code)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                return (int(row.get("종가", 0)),
                        float(row.get("등락률", 0)),
                        int(row.get("거래대금", 0)))
        except Exception:
            pass
        return (0, 0.0, 0)
