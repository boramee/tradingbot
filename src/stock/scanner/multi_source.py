"""멀티소스 종목 발굴기 — pykrx 기반

4가지 소스에서 종목을 발굴하고, 중복 제거 + 합산 점수로 최종 후보를 만든다.

소스:
  1. 외국인 순매수 상위 (5일 누적) — 스윙 핵심
  2. 기관 순매수 상위 (5일 누적) — 수급 보강
  3. 52주 신고가 근접 종목 — 돌파 후 눌림목
  4. 낙폭과대 반등 후보 — 급락 후 반등 기대

주의: pykrx는 KRX 웹사이트를 크롤링하므로 장중 당일 데이터가 불완전할 수 있음.
      → 전 영업일 기준으로 조회하고, 모든 호출에 개별 에러 핸들링 적용.
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


def _last_business_day() -> date:
    """전 영업일 (주말 제외). 장중에도 전일 데이터가 확정돼 있으므로 안전."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:  # 토=5, 일=6
        d -= timedelta(days=1)
    return d


def _safe_int(val, default=0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


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


class MultiSourceScanner:
    """pykrx 기반 멀티소스 종목 발굴기"""

    def __init__(self):
        self._cache: Dict[str, List[SourceResult]] = {}
        self._cache_time: float = 0
        self._cache_ttl = 1800  # 30분 캐시

    def scan_all(self, force: bool = False) -> Dict[str, List[SourceResult]]:
        """모든 소스에서 스캔."""
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
        logger.info("[멀티소스] 스캔 완료: 외인 %d, 기관 %d, 52주신고가 %d, 낙폭과대 %d (총 %d)",
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
            if source_count >= 3:
                item.score += 30
                item.reasons.append("멀티소스%d개" % source_count)
            elif source_count >= 2:
                item.score += 15
                item.reasons.append("멀티소스%d개" % source_count)

        result = sorted(merged.values(), key=lambda x: x.score, reverse=True)

        if result:
            logger.info("[멀티소스] 병합 %d종목: %s",
                        len(result),
                        " / ".join("%s(%.0f점)" % (r.name, r.score) for r in result[:5]))
        return result[:limit]

    # ── 소스1: 외국인 순매수 상위 ──

    def _scan_foreign_buying(self) -> List[SourceResult]:
        """최근 5일 외국인 순매수 금액 상위 종목"""
        return self._scan_investor_buying("외국인", "foreign", base_score=30)

    # ── 소스2: 기관 순매수 상위 ──

    def _scan_inst_buying(self) -> List[SourceResult]:
        """최근 5일 기관 순매수 금액 상위 종목"""
        return self._scan_investor_buying("기관합계", "inst", base_score=20)

    def _scan_investor_buying(self, investor: str, source_tag: str,
                              base_score: int = 25) -> List[SourceResult]:
        """투자자별 순매수 상위 공통 로직"""
        try:
            from pykrx import stock as pykrx_stock
        except ImportError:
            logger.warning("[%s순매수] pykrx 미설치", investor)
            return []

        end = _last_business_day()
        start = end - timedelta(days=10)
        end_str = end.strftime("%Y%m%d")
        start_str = start.strftime("%Y%m%d")

        results = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
                    start_str, end_str, market, investor)
            except Exception as e:
                logger.debug("[%s순매수] %s 조회 실패: %s", investor, market, e)
                continue

            if df is None or df.empty:
                continue

            # 컬럼명 호환: 구버전 pykrx는 다른 이름일 수 있음
            net_col = None
            for candidate in ("순매수거래대금", "순매수"):
                if candidate in df.columns:
                    net_col = candidate
                    break
            if net_col is None:
                logger.debug("[%s순매수] 컬럼 없음: %s", investor, list(df.columns))
                continue

            try:
                top = df.nlargest(20, net_col)
            except Exception:
                continue

            for code, row in top.iterrows():
                try:
                    name = str(row.get("종목명", ""))
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue
                    net_amount = _safe_int(row[net_col])
                    if net_amount <= 0:
                        continue

                    score = base_score
                    net_billion = net_amount / 100_000_000
                    if net_billion >= 500:
                        score += 25
                    elif net_billion >= 100:
                        score += 15
                    else:
                        score += 5
                    reasons = ["%s순매수%s억" % (
                        "외인" if investor == "외국인" else "기관",
                        "{:,.0f}".format(net_billion))]

                    results.append(SourceResult(
                        code=code, name=name, price=0,
                        score=score, reasons=reasons, source=source_tag,
                    ))
                except Exception:
                    continue

        # 가격 정보 보강 (전일 OHLCV 한 번에)
        results = self._fill_prices(results)
        # 저가주 제거
        results = [r for r in results if r.price >= MIN_PRICE]

        logger.info("[%s순매수] %d종목 발굴", investor, len(results))
        return results[:15]

    # ── 소스3: 52주 신고가 근접 ──

    def _scan_52week_high(self) -> List[SourceResult]:
        """52주 신고가 대비 95% 이상 (돌파 임박 또는 갱신 직후)"""
        try:
            from pykrx import stock as pykrx_stock
        except ImportError:
            logger.warning("[52주신고가] pykrx 미설치")
            return []

        last_bd = _last_business_day()
        last_bd_str = last_bd.strftime("%Y%m%d")
        year_ago_str = (last_bd - timedelta(days=365)).strftime("%Y%m%d")

        results = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                ohlcv = pykrx_stock.get_market_ohlcv_by_ticker(last_bd_str, market)
            except Exception as e:
                logger.debug("[52주신고가] %s OHLCV 실패: %s", market, e)
                continue
            if ohlcv is None or ohlcv.empty:
                continue

            try:
                cap_df = pykrx_stock.get_market_cap_by_ticker(last_bd_str, market)
            except Exception:
                cap_df = None

            # 시가총액으로 먼저 필터링 (전종목 52주 조회는 너무 느림)
            large_caps = set()
            if cap_df is not None and not cap_df.empty:
                cap_col = "시가총액" if "시가총액" in cap_df.columns else None
                if cap_col:
                    for code in cap_df.index:
                        if _safe_int(cap_df.loc[code].get(cap_col, 0)) >= MIN_MARKET_CAP:
                            large_caps.add(code)
            if not large_caps:
                continue

            # OHLCV 컬럼명 확인
            close_col = "종가" if "종가" in ohlcv.columns else "close" if "close" in ohlcv.columns else None
            if not close_col:
                logger.debug("[52주신고가] %s OHLCV 컬럼: %s", market, list(ohlcv.columns))
                continue

            checked = 0
            for code in ohlcv.index:
                if code not in large_caps:
                    continue
                row = ohlcv.loc[code]
                close = _safe_int(row.get(close_col, 0))
                if close < MIN_PRICE:
                    continue

                change_col = "등락률" if "등락률" in ohlcv.columns else "change"
                change_pct = _safe_float(row.get(change_col, 0))
                vol_col = "거래량" if "거래량" in ohlcv.columns else "volume"
                volume = _safe_int(row.get(vol_col, 0))
                tv_col = "거래대금" if "거래대금" in ohlcv.columns else "value"
                trade_val = _safe_int(row.get(tv_col, 0))

                # 52주 최고가 조회 (API 부하 제한)
                if checked >= 50:
                    break
                checked += 1

                try:
                    hist = pykrx_stock.get_market_ohlcv(year_ago_str, last_bd_str, code)
                except Exception:
                    continue
                if hist is None or len(hist) < 20:
                    continue

                high_col = "고가" if "고가" in hist.columns else "high"
                if high_col not in hist.columns:
                    continue
                high_52w = _safe_int(hist[high_col].max())
                if high_52w <= 0:
                    continue

                ratio = close / high_52w
                if ratio < 0.95 or ratio > 1.05:
                    continue

                try:
                    name = pykrx_stock.get_market_ticker_name(code)
                except Exception:
                    name = code
                if any(kw in name for kw in ETF_KEYWORDS):
                    continue

                score = 25
                reasons = []
                if ratio >= 1.0:
                    score += 20
                    reasons.append("52주신고가갱신")
                elif ratio >= 0.97:
                    score += 10
                    reasons.append("52주고가근접")
                else:
                    reasons.append("52주고가95%%")

                if trade_val >= 10_000_000_000:
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

    # ── 소스4: 낙폭과대 반등 후보 ──

    def _scan_oversold_bounce(self) -> List[SourceResult]:
        """최근 5일 급락(-3%~-15%) + 시총 5000억 이상 → 반등 기대"""
        try:
            from pykrx import stock as pykrx_stock
        except ImportError:
            logger.warning("[낙폭과대] pykrx 미설치")
            return []

        last_bd = _last_business_day()
        last_bd_str = last_bd.strftime("%Y%m%d")
        five_ago_str = (last_bd - timedelta(days=8)).strftime("%Y%m%d")

        results = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = pykrx_stock.get_market_price_change_by_ticker(
                    five_ago_str, last_bd_str, market)
            except Exception as e:
                logger.debug("[낙폭과대] %s 조회 실패: %s", market, e)
                continue
            if df is None or df.empty:
                continue

            try:
                cap_df = pykrx_stock.get_market_cap_by_ticker(last_bd_str, market)
            except Exception:
                cap_df = None

            # 컬럼명 확인
            change_col = "등락률" if "등락률" in df.columns else None
            close_col = "종가" if "종가" in df.columns else None
            if not change_col or not close_col:
                logger.debug("[낙폭과대] %s 컬럼: %s", market, list(df.columns))
                continue

            for code in df.index:
                try:
                    row = df.loc[code]
                    change = _safe_float(row.get(change_col, 0))
                    close = _safe_int(row.get(close_col, 0))

                    if close < MIN_PRICE:
                        continue
                    if change > -3 or change < -15:
                        continue

                    # 시총 5000억 이상
                    cap = 0
                    cap_col = "시가총액" if cap_df is not None and "시가총액" in cap_df.columns else None
                    if cap_col and code in cap_df.index:
                        cap = _safe_int(cap_df.loc[code].get(cap_col, 0))
                        if cap < 500_000_000_000:
                            continue
                    else:
                        continue  # 시총 확인 불가면 스킵

                    try:
                        name = pykrx_stock.get_market_ticker_name(code)
                    except Exception:
                        name = code
                    if any(kw in name for kw in ETF_KEYWORDS):
                        continue

                    tv_col = "거래대금" if "거래대금" in df.columns else None
                    trade_val = _safe_int(row.get(tv_col, 0)) if tv_col else 0
                    vol_col = "거래량" if "거래량" in df.columns else None
                    volume = _safe_int(row.get(vol_col, 0)) if vol_col else 0

                    score = 20
                    reasons = ["5일낙폭%.1f%%" % change]

                    if change <= -10:
                        score += 15
                    elif change <= -7:
                        score += 10
                    elif change <= -5:
                        score += 5

                    if cap >= 1_000_000_000_000:
                        score += 10
                        reasons.append("시총%s조" % "{:,.1f}".format(cap / 1_000_000_000_000))

                    results.append(SourceResult(
                        code=code, name=name, price=close,
                        change_pct=change, trade_value=trade_val,
                        volume=volume, score=score,
                        reasons=reasons, source="oversold",
                    ))
                except Exception:
                    continue

        results.sort(key=lambda x: x.score, reverse=True)
        logger.info("[낙폭과대] %d종목 발굴", len(results))
        return results[:15]

    # ── 유틸 ──

    @staticmethod
    def _fill_prices(items: List[SourceResult]) -> List[SourceResult]:
        """가격 없는 항목에 전일 종가 채우기 (한 번에 조회)"""
        need_price = [r for r in items if r.price == 0]
        if not need_price:
            return items
        try:
            from pykrx import stock as pykrx_stock
            last_bd_str = _last_business_day().strftime("%Y%m%d")

            # 전종목 시세 한 번에 (KOSPI + KOSDAQ)
            price_map = {}
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    ohlcv = pykrx_stock.get_market_ohlcv_by_ticker(last_bd_str, market)
                    if ohlcv is not None and not ohlcv.empty:
                        close_col = "종가" if "종가" in ohlcv.columns else None
                        change_col = "등락률" if "등락률" in ohlcv.columns else None
                        tv_col = "거래대금" if "거래대금" in ohlcv.columns else None
                        if close_col:
                            for code in ohlcv.index:
                                price_map[code] = {
                                    "price": _safe_int(ohlcv.loc[code].get(close_col, 0)),
                                    "change": _safe_float(ohlcv.loc[code].get(change_col, 0)) if change_col else 0,
                                    "tv": _safe_int(ohlcv.loc[code].get(tv_col, 0)) if tv_col else 0,
                                }
                except Exception:
                    continue

            for r in items:
                if r.price == 0 and r.code in price_map:
                    r.price = price_map[r.code]["price"]
                    r.change_pct = price_map[r.code]["change"]
                    r.trade_value = price_map[r.code]["tv"]
        except Exception as e:
            logger.debug("[멀티소스] 가격 보강 실패: %s", e)

        return items
