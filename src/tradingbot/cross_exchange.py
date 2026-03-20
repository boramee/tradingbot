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


def arb_min_net_spread_pct() -> float | None:
    raw = os.getenv("ARB_MIN_NET_SPREAD_PCT", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def default_taker_fee_pct() -> float:
    """테이커 수수료(%%). 예: 0.1 → 0.1%."""
    raw = os.getenv("ARB_TAKER_FEE_PCT", "0.1").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.1


def fee_overrides_map() -> dict[str, float]:
    """
    ARB_FEE_OVERRIDES=binance:0.04,kraken:0.26,upbit:0.05
    거래소별 테이커 수수료(%%).
    """
    raw = os.getenv("ARB_FEE_OVERRIDES", "").strip()
    if not raw:
        return {}
    out: dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        try:
            out[k] = float(v)
        except ValueError:
            continue
    return out


def taker_fee_pct_for(exchange_id: str) -> float:
    m = fee_overrides_map()
    return m.get(exchange_id.lower(), default_taker_fee_pct())


def net_edge_pct_from_mids(
    buy_mid: float,
    sell_mid: float,
    buy_fee_pct: float,
    sell_fee_pct: float,
) -> float:
    """
    USDT(또는 동일 quote) 기준 스팟: 저가 거래소에서 매수 → 고가 거래소에서 매도.
    테이커 가정: 매수는 (1+f_buy), 매도는 (1-f_sell) 배율로 단순 모델링.
    """
    eff_buy = buy_mid * (1.0 + buy_fee_pct / 100.0)
    eff_sell = sell_mid * (1.0 - sell_fee_pct / 100.0)
    if eff_buy <= 0:
        return 0.0
    return (eff_sell - eff_buy) / eff_buy * 100.0


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
        d = {
            "symbol": self.symbol,
            "venues": [v.as_dict() for v in self.venues],
            "min_exchange": self.min_exchange,
            "max_exchange": self.max_exchange,
            "min_mid": self.min_mid,
            "max_mid": self.max_mid,
            "spread_pct": self.spread_pct,
        }
        opp = opportunity_from_cross_report(self)
        if opp is not None:
            d["opportunity"] = opp.as_dict()
        return d

    def opportunity(self) -> "ArbOpportunity | None":
        return opportunity_from_cross_report(self)


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
class ArbOpportunity:
    """거래소 간 동일 심볼 mid 차익 후보(테이커 수수료 단순 차감)."""

    kind: str  # "cross_mid"
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_mid: float
    sell_mid: float
    gross_spread_pct: float
    buy_taker_fee_pct: float
    sell_taker_fee_pct: float
    net_edge_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "buy_mid": self.buy_mid,
            "sell_mid": self.sell_mid,
            "gross_spread_pct": self.gross_spread_pct,
            "buy_taker_fee_pct": self.buy_taker_fee_pct,
            "sell_taker_fee_pct": self.sell_taker_fee_pct,
            "net_edge_pct": self.net_edge_pct,
        }


def opportunity_from_cross_report(r: CrossSpreadReport) -> ArbOpportunity | None:
    if (
        r.spread_pct is None
        or r.min_exchange is None
        or r.max_exchange is None
        or r.min_mid is None
        or r.max_mid is None
    ):
        return None
    bf = taker_fee_pct_for(r.min_exchange)
    sf = taker_fee_pct_for(r.max_exchange)
    net = net_edge_pct_from_mids(r.min_mid, r.max_mid, bf, sf)
    return ArbOpportunity(
        kind="cross_mid",
        symbol=r.symbol,
        buy_exchange=r.min_exchange,
        sell_exchange=r.max_exchange,
        buy_mid=r.min_mid,
        sell_mid=r.max_mid,
        gross_spread_pct=r.spread_pct,
        buy_taker_fee_pct=bf,
        sell_taker_fee_pct=sf,
        net_edge_pct=net,
    )


def opportunities_from_reports(reports: list[CrossSpreadReport]) -> list[ArbOpportunity]:
    return [o for r in reports if (o := opportunity_from_cross_report(r)) is not None]


@dataclass(frozen=True)
class KimchiOpportunity:
    """업비트 암시 USDT 가격 vs 해외 USDT 마켓 (같은 단순 테이커 모델)."""

    kind: str  # "kimchi"
    base: str
    global_exchange: str
    symbol_global: str
    buy_exchange: str
    sell_exchange: str
    buy_mid: float
    sell_mid: float
    gross_spread_pct: float
    buy_taker_fee_pct: float
    sell_taker_fee_pct: float
    net_edge_pct: float
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base": self.base,
            "global_exchange": self.global_exchange,
            "symbol_global": self.symbol_global,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "buy_mid": self.buy_mid,
            "sell_mid": self.sell_mid,
            "gross_spread_pct": self.gross_spread_pct,
            "buy_taker_fee_pct": self.buy_taker_fee_pct,
            "sell_taker_fee_pct": self.sell_taker_fee_pct,
            "net_edge_pct": self.net_edge_pct,
            "note": self.note,
        }


def opportunity_from_kimchi_report(r: KimchiReport) -> KimchiOpportunity | None:
    if (
        r.upbit_implied_usdt is None
        or r.global_usdt is None
        or r.spread_pct is None
    ):
        return None
    gex = r.global_exchange
    sym_g = f"{r.base}/USDT"
    # spread_pct = (upbit_implied - global) / global * 100  → 업비트가 더 비쌀 때 양수
    if r.spread_pct >= 0:
        buy_ex, sell_ex = gex, "upbit"
        buy_mid, sell_mid = r.global_usdt, r.upbit_implied_usdt
        gross = r.spread_pct
        note = "업비트가 해외보다 비쌈: 해외에서 매수 → 업비트에서 매도(이론, 이체·규제 미반영)"
    else:
        buy_ex, sell_ex = "upbit", gex
        buy_mid, sell_mid = r.upbit_implied_usdt, r.global_usdt
        gross = (sell_mid - buy_mid) / buy_mid * 100.0 if buy_mid > 0 else r.spread_pct
        note = "해외가 더 비쌈: 업비트에서 매수 → 해외에서 매도(이론, 이체·규제 미반영)"
    bf = taker_fee_pct_for(buy_ex)
    sf = taker_fee_pct_for(sell_ex)
    net = net_edge_pct_from_mids(buy_mid, sell_mid, bf, sf)
    return KimchiOpportunity(
        kind="kimchi",
        base=r.base,
        global_exchange=gex,
        symbol_global=sym_g,
        buy_exchange=buy_ex,
        sell_exchange=sell_ex,
        buy_mid=buy_mid,
        sell_mid=sell_mid,
        gross_spread_pct=gross,
        buy_taker_fee_pct=bf,
        sell_taker_fee_pct=sf,
        net_edge_pct=net,
        note=note,
    )


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
        d: dict[str, Any] = {
            "base": self.base,
            "upbit_implied_usdt": self.upbit_implied_usdt,
            "global_usdt": self.global_usdt,
            "global_exchange": self.global_exchange,
            "spread_pct": self.spread_pct,
            "detail": self.detail,
            "errors": list(self.errors),
        }
        ko = opportunity_from_kimchi_report(self)
        if ko is not None:
            d["opportunity"] = ko.as_dict()
        return d


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


def watch_signals(
    *,
    interval_sec: float,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    min_net_spread_pct: float | None,
    emit: Callable[[list[ArbOpportunity]], None],
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """순스프레드(수수료 차감 후) 기준으로만 알림."""
    while True:
        reps = scan_cross_spread(symbols=symbols, exchanges=exchanges)
        opps = opportunities_from_reports(reps)
        if min_net_spread_pct is None:
            if opps:
                emit(opps)
        else:
            hit = [o for o in opps if o.net_edge_pct >= min_net_spread_pct]
            if hit:
                emit(hit)
        if should_stop and should_stop():
            break
        time.sleep(max(0.5, interval_sec))
