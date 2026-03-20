from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from tradingbot.config import Settings
from tradingbot.cross_exchange import (
    CrossSpreadReport,
    arb_exchange_ids,
    arb_min_spread_pct,
    arb_symbols,
    scan_cross_spread,
    scan_kimchi_vs_global,
    watch_cross_spread,
)
from tradingbot.exchange_client import build_exchange
from tradingbot.usdt_premium import scan_usdt_premiums


def _cmd_ping(settings: Settings) -> int:
    ex = build_exchange(settings, public_only=not settings.has_credentials())
    ex.load_markets()
    mode = "public" if not settings.has_credentials() else "authenticated"
    print(f"거래소: {ex.id} | 마켓 수: {len(ex.markets)} | 모드: {mode}")
    return 0


def _cmd_balance(settings: Settings) -> int:
    settings.require_credentials()
    ex = build_exchange(settings)
    bal = ex.fetch_balance()
    # 잔고만 요약 (너무 길면 전체 자산 중 0 아닌 것)
    free = {k: v for k, v in bal.get("free", {}).items() if v}
    used = {k: v for k, v in bal.get("used", {}).items() if v}
    out: dict[str, Any] = {"free": free, "used": used}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _cmd_ticker(settings: Settings, symbol: str) -> int:
    ex = build_exchange(settings, public_only=not settings.has_credentials())
    t = ex.fetch_ticker(symbol)
    keep = {k: t[k] for k in ("symbol", "last", "bid", "ask", "quoteVolume") if k in t}
    print(json.dumps(keep, indent=2, ensure_ascii=False))
    return 0


def _cmd_order(
    settings: Settings,
    symbol: str,
    side: str,
    amount: float,
    order_type: str,
) -> int:
    settings.require_credentials()
    if settings.dry_run:
        print(
            "[DRY_RUN] 주문을 보내지 않았습니다.",
            json.dumps(
                {
                    "symbol": symbol,
                    "side": side,
                    "amount": amount,
                    "type": order_type,
                },
                ensure_ascii=False,
            ),
        )
        return 0
    settings.assert_live_order_allowed()
    ex = build_exchange(settings)
    order = ex.create_order(symbol, order_type, side, amount)
    print(json.dumps(order, indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_premium(*, use_fx: bool, as_json: bool) -> int:
    rows, warnings = scan_usdt_premiums(fetch_fx=use_fx)
    for w in warnings:
        print(f"경고: {w}", file=sys.stderr)
    payload = [r.as_dict() for r in rows]
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    # 프리미엄 높은 순(없는 항목은 맨 뒤)
    def sort_key(d: dict) -> tuple[int, float, str]:
        p = d.get("premium_pct")
        if p is None:
            return (1, 0.0, d["exchange"] + d["symbol"])
        return (0, -float(p), d["exchange"] + d["symbol"])

    for d in sorted(payload, key=sort_key):
        ex, sym, q = d["exchange"], d["symbol"], d["quote"]
        err = d.get("error")
        if err:
            print(f"{ex:12} {sym:24}  오류: {err}")
            continue
        last, prem = d.get("last"), d.get("premium_pct")
        fair = d.get("fair_usdt_in_quote")
        src = d.get("source", "")
        prem_s = f"{prem:+.3f}%" if prem is not None else "n/a"
        fair_s = f"{fair:.6g}" if fair is not None else "n/a"
        print(
            f"{ex:12} {sym:26} {q:4}  last={last:.6g}  fair≈{fair_s}  premium={prem_s}  ({src})"
        )
    return 0


def _parse_csv_opt(s: str | None) -> list[str] | None:
    if not s or not str(s).strip():
        return None
    return [x.strip() for x in str(s).split(",") if x.strip()]


def _print_spread_reports(reports: list[CrossSpreadReport], *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                [r.as_dict() for r in reports],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    for r in reports:
        sym = r.symbol
        if r.spread_pct is None:
            print(f"\n=== {sym} (비교 불가: 유효 호가 2곳 미만 또는 오류) ===")
            for v in r.venues:
                if v.error:
                    print(f"  {v.exchange_id:12}  {v.symbol:14}  {v.error}")
                else:
                    print(f"  {v.exchange_id:12}  mid={v.mid}")
            continue
        print(
            f"\n=== {sym}  스프레드 {r.spread_pct:.4f}%  "
            f"(저가 {r.min_exchange} {r.min_mid:.8g}  /  고가 {r.max_exchange} {r.max_mid:.8g}) ==="
        )
        for v in r.venues:
            if v.mid is not None:
                print(f"  {v.exchange_id:12}  mid={v.mid:.12g}")
            else:
                print(f"  {v.exchange_id:12}  --  {v.error or 'n/a'}")


def _cmd_spread(
    *,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    as_json: bool,
    watch: bool,
    interval: float,
    min_pct: float | None,
) -> int:
    syms = symbols or arb_symbols()
    exs = exchanges or arb_exchange_ids()
    eff_min = min_pct if min_pct is not None else (arb_min_spread_pct() if watch else None)

    def emit(reps: list[CrossSpreadReport]) -> None:
        _print_spread_reports(reps, as_json=as_json)

    if not watch:
        reps = scan_cross_spread(symbols=syms, exchanges=exs)
        if eff_min is not None:
            reps = [r for r in reps if r.spread_pct is not None and r.spread_pct >= eff_min]
        emit(reps)
        return 0

    try:
        watch_cross_spread(
            interval_sec=interval,
            symbols=syms,
            exchanges=exs,
            min_spread_pct=eff_min,
            emit=emit,
        )
    except KeyboardInterrupt:
        print("\n중단됨.", file=sys.stderr)
        return 0
    return 0


def _cmd_kimchi(
    *,
    base: str,
    global_ex: str | None,
    as_json: bool,
    watch: bool,
    interval: float,
    min_spread_abs: float | None,
) -> int:
    def once() -> dict[str, Any]:
        r = scan_kimchi_vs_global(base=base, global_exchange=global_ex)
        return r.as_dict()

    def should_emit(d: dict[str, Any]) -> bool:
        sp = d.get("spread_pct")
        if min_spread_abs is None:
            return True
        return sp is not None and abs(float(sp)) >= min_spread_abs

    try:
        while True:
            d = once()
            if should_emit(d):
                if as_json:
                    print(json.dumps(d, indent=2, ensure_ascii=False))
                else:
                    if d.get("errors"):
                        print("오류:", "; ".join(d["errors"]), file=sys.stderr)
                    imp, gl, sp = d.get("upbit_implied_usdt"), d.get("global_usdt"), d.get("spread_pct")
                    gex = d.get("global_exchange")
                    if imp is not None and gl is not None and sp is not None:
                        print(
                            f"{d['base']}: 업비트 암시 USDT={imp:.8g}  |  "
                            f"{gex} {d['base']}/USDT={gl:.8g}  |  차이(김프)={sp:+.4f}%"
                        )
                        if d.get("detail"):
                            print("  detail:", json.dumps(d["detail"], ensure_ascii=False))
                    else:
                        print(json.dumps(d, indent=2, ensure_ascii=False))
            if not watch:
                break
            time.sleep(max(0.5, interval))
    except KeyboardInterrupt:
        print("\n중단됨.", file=sys.stderr)
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="거래소 API 연동 스타터 (기본 드라이런).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="마켓 로드 등 연결 확인")

    sub.add_parser("balance", help="잔고 조회")

    p_t = sub.add_parser("ticker", help="티커 조회")
    p_t.add_argument("symbol", help="예: BTC/USDT")

    p_o = sub.add_parser("order", help="시장가 주문 (DRY_RUN 시 시뮬레이션)")
    p_o.add_argument("symbol", help="예: BTC/USDT")
    p_o.add_argument("side", choices=["buy", "sell"])
    p_o.add_argument("amount", type=float, help="수량 (기준: base, 예: BTC 수량)")
    p_o.add_argument(
        "--type",
        dest="order_type",
        default="market",
        choices=["market"],
        help="현재는 market 만 지원",
    )

    p_sp = sub.add_parser(
        "spread",
        help="여러 거래소에서 동일 심볼(예: BTC/USDT) mid 가격 차이·스프레드(%) 스캔",
    )
    p_sp.add_argument(
        "--symbols",
        help="쉼표 구분. 미설정 시 ARB_SYMBOLS 또는 기본 BTC/USDT,ETH/USDT",
    )
    p_sp.add_argument(
        "--exchanges",
        help="쉼표 구분. 미설정 시 ARB_EXCHANGES 또는 기본 다국적 거래소 목록",
    )
    p_sp.add_argument("--json", action="store_true", dest="spread_json")
    p_sp.add_argument(
        "--watch",
        action="store_true",
        help="주기적으로 반복 (Ctrl+C 종료)",
    )
    p_sp.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="watch 시 갱신 주기(초)",
    )
    p_sp.add_argument(
        "--min-pct",
        type=float,
        default=None,
        dest="spread_min_pct",
        help="스프레드가 이 값(%%) 이상일 때만 출력(watch 시 유용). 미지정이면 watch는 ARB_MIN_SPREAD_PCT 사용",
    )

    p_kc = sub.add_parser(
        "kimchi",
        help="업비트 KRW 마켓을 USDT로 환산한 가격 vs 해외 거래소 USDT 마켓(김치 프리미엄 스타일)",
    )
    p_kc.add_argument("--base", default="BTC", help="예: BTC, ETH")
    p_kc.add_argument(
        "--global-exchange",
        default=None,
        help="비교할 해외 거래소 ccxt ID (기본 binance 또는 ARB_GLOBAL_EXCHANGE)",
    )
    p_kc.add_argument("--json", action="store_true", dest="kimchi_json")
    p_kc.add_argument("--watch", action="store_true")
    p_kc.add_argument("--interval", type=float, default=5.0)
    p_kc.add_argument(
        "--min-abs-pct",
        type=float,
        default=None,
        help="|스프레드|가 이 값 이상일 때만 출력",
    )

    p_pr = sub.add_parser(
        "premium",
        help="(보조) USDT/법정화폐 호가 vs USD 환율로 이론가 대비 괴리(%) — 거래소 간 차익과는 별개",
    )
    p_pr.add_argument(
        "--no-fx",
        action="store_true",
        help="환율 API 호출 생략(원시 last 만)",
    )
    p_pr.add_argument(
        "--json",
        action="store_true",
        dest="premium_json",
        help="JSON 으로 출력",
    )

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    try:
        if args.cmd == "ping":
            return _cmd_ping(settings)
        if args.cmd == "balance":
            return _cmd_balance(settings)
        if args.cmd == "ticker":
            return _cmd_ticker(settings, args.symbol)
        if args.cmd == "order":
            return _cmd_order(
                settings,
                args.symbol,
                args.side,
                args.amount,
                args.order_type,
            )
        if args.cmd == "spread":
            return _cmd_spread(
                symbols=_parse_csv_opt(args.symbols),
                exchanges=_parse_csv_opt(args.exchanges),
                as_json=args.spread_json,
                watch=args.watch,
                interval=args.interval,
                min_pct=args.spread_min_pct,
            )
        if args.cmd == "kimchi":
            return _cmd_kimchi(
                base=str(args.base).strip().upper(),
                global_ex=args.global_exchange,
                as_json=args.kimchi_json,
                watch=args.watch,
                interval=args.interval,
                min_spread_abs=args.min_abs_pct,
            )
        if args.cmd == "premium":
            return _cmd_premium(
                use_fx=not args.no_fx,
                as_json=args.premium_json,
            )
    except Exception as e:  # noqa: BLE001 — CLI 에서 사용자에게 메시지 전달
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
