from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import ccxt


def arb_exchange_ids() -> list[str]:
    raw = os.getenv("ARB_EXCHANGES", "").strip()
    if raw:
        return [x.strip().lower() for x in raw.split(",") if x.strip()]
    return ["binance", "okx", "bybit", "kraken", "bitfinex", "upbit", "bithumb"]


def arb_symbols() -> list[str]:
    raw = os.getenv("ARB_SYMBOLS", "").strip()
    if raw:
        return [x.strip().upper().replace(" ", "") for x in raw.split(",") if x.strip()]
    return ["BTC/USDT", "ETH/USDT"]


def arb_min_spread_pct() -> float | None:
    raw = os.getenv("ARB_MIN_SPREAD_PCT", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _build_public(exchange_id: str) -> ccxt.Exchange:
    klass = getattr(ccxt, exchange_id, None)
    if klass is None:
        raise RuntimeError(f"알 수 없는 거래소: {exchange_id!r}")
    return klass({"enableRateLimit": True})


def _mid_from_ticker(t: dict[str, Any]) -> float | None:
    bid, ask, last = t.get("bid"), t.get("ask"), t.get("last") or t.get("close")
    try:
        if bid is not None and ask is not None:
            b, a = float(bid), float(ask)
            if b > 0 and a > 0:
                return (b + a) / 2.0
        if last is not None:
            v = float(last)
            return v if v > 0 else None
    except (TypeError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class VenueQuote:
    exchange_id: str
    symbol: str
    mid: float | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange_id,
            "symbol": self.symbol,
            "mid": self.mid,
            "error": self.error,
        }


@dataclass(frozen=True)
class CrossSpreadReport:
    """동일 ccxt 심볼(예: BTC/USDT)을 여러 거래소에서 비교한 결과."""

    symbol: str
    venues: tuple[VenueQuote, ...]
    min_exchange: str | None
    max_exchange: str | None
    min_mid: float | None
    max_mid: float | None
    spread_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "venues": [v.as_dict() for v in self.venues],
            "min_exchange": self.min_exchange,
            "max_exchange": self.max_exchange,
            "min_mid": self.min_mid,
            "max_mid": self.max_mid,
            "spread_pct": self.spread_pct,
        }


def _quote_one(exchange_id: str, symbol: str) -> VenueQuote:
    try:
        ex = _build_public(exchange_id)
        ex.load_markets()
        if symbol not in ex.markets:
            return VenueQuote(exchange_id, symbol, None, error="마켓 없음")
        t = ex.fetch_ticker(symbol)
        mid = _mid_from_ticker(t)
        if mid is None:
            return VenueQuote(exchange_id, symbol, None, error="가격 없음")
        return VenueQuote(exchange_id, symbol, mid, None)
    except Exception as e:  # noqa: BLE001
        return VenueQuote(exchange_id, symbol, None, error=str(e))


def scan_cross_spread(
    symbols: list[str] | None = None,
    exchanges: list[str] | None = None,
) -> list[CrossSpreadReport]:
    syms = symbols if symbols is not None else arb_symbols()
    exs = exchanges if exchanges is not None else arb_exchange_ids()
    reports: list[CrossSpreadReport] = []
    for sym in syms:
        venues = tuple(_quote_one(eid, sym) for eid in exs)
        ok = [v for v in venues if v.mid is not None]
        if len(ok) < 2:
            reports.append(
                CrossSpreadReport(
                    sym,
                    venues,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )
            continue
        min_v = min(ok, key=lambda x: x.mid or 0.0)
        max_v = max(ok, key=lambda x: x.mid or 0.0)
        assert min_v.mid is not None and max_v.mid is not None
        lo, hi = min_v.mid, max_v.mid
        spread = (hi - lo) / lo * 100.0 if lo > 0 else None
        reports.append(
            CrossSpreadReport(
                sym,
                venues,
                min_v.exchange_id,
                max_v.exchange_id,
                lo,
                hi,
                spread,
            )
        )
    return reports


@dataclass(frozen=True)
class KimchiReport:
    """업비트 KRW 마켓을 USDT로 환산한 뒤 해외 BTC/USDT와 비교 (김치 프리미엄 스타일)."""

    base: str
    upbit_implied_usdt: float | None
    global_usdt: float | None
    global_exchange: str
    spread_pct: float | None
    detail: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "upbit_implied_usdt": self.upbit_implied_usdt,
            "global_usdt": self.global_usdt,
            "global_exchange": self.global_exchange,
            "spread_pct": self.spread_pct,
            "detail": self.detail,
            "errors": list(self.errors),
        }


def scan_kimchi_vs_global(
    base: str = "BTC",
    global_exchange: str | None = None,
) -> KimchiReport:
    """
    upbit: base/KRW ÷ USDT/KRW ≈ base의 USDT 가격, 이를 해외 거래소 base/USDT와 비교.
    """
    gex = (global_exchange or os.getenv("ARB_GLOBAL_EXCHANGE", "binance")).strip().lower()
    detail: dict[str, Any] = {}

    try:
        upbit = _build_public("upbit")
        upbit.load_markets()
        sym_krw = f"{base.upper()}/KRW"
        sym_usdt_krw = "USDT/KRW"
        if sym_krw not in upbit.markets:
            return KimchiReport(
                base.upper(),
                None,
                None,
                gex,
                None,
                detail,
                (f"업비트에 {sym_krw} 없음",),
            )
        if sym_usdt_krw not in upbit.markets:
            return KimchiReport(
                base.upper(),
                None,
                None,
                gex,
                None,
                detail,
                ("업비트에 USDT/KRW 없음",),
            )
        t_b = upbit.fetch_ticker(sym_krw)
        t_u = upbit.fetch_ticker(sym_usdt_krw)
        base_krw = _mid_from_ticker(t_b)
        usdt_krw = _mid_from_ticker(t_u)
        detail["upbit_base_krw"] = base_krw
        detail["upbit_usdt_krw"] = usdt_krw
        if base_krw is None or usdt_krw is None or usdt_krw <= 0:
            return KimchiReport(
                base.upper(),
                None,
                None,
                gex,
                None,
                detail,
                ("업비트 호가 파싱 실패",),
            )
        implied = base_krw / usdt_krw
    except Exception as e:  # noqa: BLE001
        return KimchiReport(base.upper(), None, None, gex, None, detail, (str(e),))

    try:
        glob_ex = _build_public(gex)
        glob_ex.load_markets()
        sym_u = f"{base.upper()}/USDT"
        if sym_u not in glob_ex.markets:
            return KimchiReport(
                base.upper(),
                implied,
                None,
                gex,
                None,
                detail,
                (f"{gex} 에 {sym_u} 없음",),
            )
        t_g = glob_ex.fetch_ticker(sym_u)
        g_mid = _mid_from_ticker(t_g)
        detail[f"{gex}_{sym_u}_mid"] = g_mid
        if g_mid is None or g_mid <= 0:
            return KimchiReport(
                base.upper(),
                implied,
                None,
                gex,
                None,
                detail,
                (f"{gex} 가격 없음",),
            )
        sp = (implied - g_mid) / g_mid * 100.0
        return KimchiReport(base.upper(), implied, g_mid, gex, sp, detail, ())
    except Exception as e:  # noqa: BLE001
        return KimchiReport(
            base.upper(),
            implied,
            None,
            gex,
            None,
            detail,
            (str(e),),
        )


def watch_cross_spread(
    *,
    interval_sec: float,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    min_spread_pct: float | None,
    emit: Callable[[list[CrossSpreadReport]], None],
    should_stop: Callable[[], bool] | None = None,
) -> None:
    while True:
        reps = scan_cross_spread(symbols=symbols, exchanges=exchanges)
        if min_spread_pct is None:
            emit(reps)
        else:
            filtered = [r for r in reps if r.spread_pct is not None and r.spread_pct >= min_spread_pct]
            if filtered:
                emit(filtered)
        if should_stop and should_stop():
            break
        time.sleep(max(0.5, interval_sec))
