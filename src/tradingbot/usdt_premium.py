from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import ccxt

from tradingbot.fx_rates import fetch_usd_rates

# ccxt 통화 코드 기준(주요 법정화폐). USDT 베이스 마켓만 스캔.
_FIAT = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CAD",
        "AUD",
        "NZD",
        "KRW",
        "TRY",
        "BRL",
        "MXN",
        "INR",
        "IDR",
        "THB",
        "VND",
        "PHP",
        "MYR",
        "SGD",
        "HKD",
        "TWD",
        "AED",
        "SAR",
        "PLN",
        "CZK",
        "HUF",
        "SEK",
        "NOK",
        "DKK",
        "ZAR",
        "ARS",
        "COP",
        "PEN",
        "CLP",
        "RUB",
        "UAH",
        "ILS",
        "NGN",
        "EGP",
    }
)


def premium_exchange_ids() -> list[str]:
    raw = os.getenv("PREMIUM_EXCHANGES", "").strip()
    if raw:
        return [x.strip().lower() for x in raw.split(",") if x.strip()]
    return ["upbit", "bithumb", "binance", "okx", "bybit", "kraken", "bitfinex"]


@dataclass(frozen=True)
class PremiumRow:
    exchange_id: str
    symbol: str
    quote: str
    last: float | None
    fair_usdt_in_quote: float | None
    premium_pct: float | None
    source: str  # "direct" | "implied_btc"
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange_id,
            "symbol": self.symbol,
            "quote": self.quote,
            "last": self.last,
            "fair_usdt_in_quote": self.fair_usdt_in_quote,
            "premium_pct": self.premium_pct,
            "source": self.source,
            "error": self.error,
        }


def _fair_usdt_in_quote(rates: dict[str, float], quote: str) -> float | None:
    q = quote.upper()
    if q == "USD":
        return 1.0
    return rates.get(q)


def _premium_pct(last: float, fair: float | None) -> float | None:
    if fair is None or fair <= 0 or last <= 0:
        return None
    return (last / fair - 1.0) * 100.0


def _usdt_fiat_symbols(exchange: ccxt.Exchange) -> list[str]:
    out: list[str] = []
    for sym, m in exchange.markets.items():
        if not m.get("active", True):
            continue
        mtype = m.get("type") or "spot"
        if mtype in ("swap", "future", "option"):
            continue
        base = (m.get("base") or "").upper()
        quote = (m.get("quote") or "").upper()
        if base == "USDT" and quote in _FIAT:
            out.append(sym)
    return sorted(set(out))


def _scan_exchange(
    exchange_id: str,
    rates: dict[str, float] | None,
) -> list[PremiumRow]:
    klass = getattr(ccxt, exchange_id, None)
    if klass is None:
        return [
            PremiumRow(
                exchange_id,
                "",
                "",
                None,
                None,
                None,
                "direct",
                error="알 수 없는 거래소 ID",
            )
        ]
    ex = klass({"enableRateLimit": True})
    try:
        ex.load_markets()
    except Exception as e:  # noqa: BLE001
        return [
            PremiumRow(
                exchange_id,
                "",
                "",
                None,
                None,
                None,
                "direct",
                error=str(e),
            )
        ]

    rows: list[PremiumRow] = []
    for sym in _usdt_fiat_symbols(ex):
        q = ex.markets[sym]["quote"].upper()
        try:
            t = ex.fetch_ticker(sym)
            last = t.get("last") or t.get("close")
            if last is None:
                rows.append(
                    PremiumRow(
                        exchange_id,
                        sym,
                        q,
                        None,
                        None,
                        None,
                        "direct",
                        error="last 가격 없음",
                    )
                )
                continue
            last_f = float(last)
            fair = _fair_usdt_in_quote(rates, q) if rates else None
            rows.append(
                PremiumRow(
                    exchange_id,
                    sym,
                    q,
                    last_f,
                    fair,
                    _premium_pct(last_f, fair),
                    "direct",
                )
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                PremiumRow(
                    exchange_id,
                    sym,
                    q,
                    None,
                    None,
                    None,
                    "direct",
                    error=str(e),
                )
            )

    # BTC/KRW ÷ BTC/USDT 로 암시적 USDT/KRW (김프 스캐너에 흔함)
    if "BTC/KRW" in ex.markets and "BTC/USDT" in ex.markets:
        try:
            krw = float(ex.fetch_ticker("BTC/KRW")["last"])
            usdt = float(ex.fetch_ticker("BTC/USDT")["last"])
            implied = krw / usdt
            fair = _fair_usdt_in_quote(rates, "KRW") if rates else None
            rows.append(
                PremiumRow(
                    exchange_id,
                    "BTC/KRW ÷ BTC/USDT",
                    "KRW",
                    implied,
                    fair,
                    _premium_pct(implied, fair),
                    "implied_btc",
                )
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                PremiumRow(
                    exchange_id,
                    "BTC/KRW ÷ BTC/USDT",
                    "KRW",
                    None,
                    None,
                    None,
                    "implied_btc",
                    error=str(e),
                )
            )

    return rows


def scan_usdt_premiums(
    *,
    fetch_fx: bool = True,
) -> tuple[list[PremiumRow], list[str]]:
    """
    여러 거래소의 USDT/법정화폐 호가를 모으고, USD 환율 대비 프리미엄(%)을 붙입니다.
    환율은 참고용(중앙은행/집계 시세 지연·편차 가능).
    """
    warnings: list[str] = []
    rates: dict[str, float] | None = None
    if fetch_fx:
        try:
            rates = fetch_usd_rates()
        except RuntimeError as e:
            warnings.append(str(e))
            rates = None

    all_rows: list[PremiumRow] = []
    for eid in premium_exchange_ids():
        all_rows.extend(_scan_exchange(eid, rates))

    return all_rows, warnings
