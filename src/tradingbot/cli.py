from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from tradingbot.config import Settings
from tradingbot.cross_exchange import (
    ArbOpportunity,
    CrossSpreadReport,
    KimchiOpportunity,
    arb_exchange_ids,
    arb_min_net_spread_pct,
    arb_min_spread_pct,
    arb_symbols,
    opportunities_from_reports,
    opportunity_from_kimchi_report,
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


def _print_spread_reports(
    reports: list[CrossSpreadReport],
    *,
    as_json: bool,
    show_net: bool,
) -> None:
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
        if show_net:
            o = r.opportunity()
            if o is not None:
                print(
                    f"  → 테이커 수수료 반영 순차익(추정): {o.net_edge_pct:+.4f}%  "
                    f"(매수 {o.buy_exchange} {o.buy_taker_fee_pct:g}% / "
                    f"매도 {o.sell_exchange} {o.sell_taker_fee_pct:g}%)"
                )


def _cmd_spread(
    *,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    as_json: bool,
    watch: bool,
    interval: float,
    min_pct: float | None,
    show_net: bool,
) -> int:
    syms = symbols or arb_symbols()
    exs = exchanges or arb_exchange_ids()
    eff_min = min_pct if min_pct is not None else (arb_min_spread_pct() if watch else None)

    def emit(reps: list[CrossSpreadReport]) -> None:
        _print_spread_reports(reps, as_json=as_json, show_net=show_net)

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


def _signal_payload(
    *,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    include_kimchi: bool,
    kimchi_base: str,
    kimchi_global: str | None,
) -> list[ArbOpportunity | KimchiOpportunity]:
    reps = scan_cross_spread(symbols=symbols, exchanges=exchanges)
    out: list[ArbOpportunity | KimchiOpportunity] = list(opportunities_from_reports(reps))
    if include_kimchi:
        kr = scan_kimchi_vs_global(base=kimchi_base, global_exchange=kimchi_global)
        if (ko := opportunity_from_kimchi_report(kr)) is not None:
            out.append(ko)
    return out


def _print_signal_human(opps: list[ArbOpportunity | KimchiOpportunity]) -> None:
    for o in opps:
        if isinstance(o, KimchiOpportunity):
            print(
                f"[kimchi] {o.base}  순={o.net_edge_pct:+.4f}%  "
                f"총={o.gross_spread_pct:+.4f}%  "
                f"매수@{o.buy_exchange} → 매도@{o.sell_exchange}"
            )
            print(f"         ({o.note})")
        else:
            print(
                f"[cross] {o.symbol}  순={o.net_edge_pct:+.4f}%  "
                f"총={o.gross_spread_pct:+.4f}%  "
                f"매수@{o.buy_exchange} → 매도@{o.sell_exchange}"
            )


def _cmd_signals(
    *,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    as_json: bool,
    watch: bool,
    interval: float,
    min_net_pct: float | None,
    include_kimchi: bool,
    kimchi_base: str,
    kimchi_global: str | None,
) -> int:
    eff_min = (
        min_net_pct
        if min_net_pct is not None
        else (arb_min_net_spread_pct() if watch else None)
    )

    def one_round() -> list[ArbOpportunity | KimchiOpportunity]:
        opps = _signal_payload(
            symbols=symbols,
            exchanges=exchanges,
            include_kimchi=include_kimchi,
            kimchi_base=kimchi_base,
            kimchi_global=kimchi_global,
        )
        if eff_min is None:
            return opps
        return [o for o in opps if o.net_edge_pct >= eff_min]

    def emit(opps: list[ArbOpportunity | KimchiOpportunity]) -> None:
        if not opps:
            return
        if as_json:
            print(json.dumps([o.as_dict() for o in opps], indent=2, ensure_ascii=False))
        else:
            _print_signal_human(opps)

    if not watch:
        emit(one_round())
        return 0

    try:
        while True:
            opps = one_round()
            emit(opps)
            time.sleep(max(0.5, interval))
    except KeyboardInterrupt:
        print("\n중단됨.", file=sys.stderr)
        return 0
    return 0


def _simulate_cross_human(o: ArbOpportunity, amount: float) -> None:
    quote = o.symbol.partition("/")[2] or "?"
    print(f"[SIM] {o.buy_exchange}: 시장가 매수  {amount}  {o.symbol}  (참고 mid {o.buy_mid:g})")
    print(f"[SIM] {o.sell_exchange}: 시장가 매도  {amount}  {o.symbol}  (참고 mid {o.sell_mid:g})")
    buy_cost = amount * o.buy_mid * (1.0 + o.buy_taker_fee_pct / 100.0)
    sell_rev = amount * o.sell_mid * (1.0 - o.sell_taker_fee_pct / 100.0)
    print(
        f"[SIM] 단순모델: 매수비용≈{buy_cost:g} {quote}, 매도수령≈{sell_rev:g} {quote}, "
        f"잔여≈{sell_rev - buy_cost:g} {quote} | 순변동률≈{o.net_edge_pct:+.4f}%  "
        "(슬리피지·이체·세금 미포함)"
    )


def _simulate_kimchi_human(o: KimchiOpportunity, amount: float) -> None:
    print(f"[SIM][kimchi] {o.note}")
    print(
        f"[SIM] {o.buy_exchange}: {amount} {o.base} 매수 "
        f"(참고 {o.symbol_global} mid {o.buy_mid:g})"
    )
    print(
        f"[SIM] {o.sell_exchange}: {amount} {o.base} 매도 "
        f"(참고 암시 USDT가 {o.sell_mid:g})"
    )
    buy_cost = amount * o.buy_mid * (1.0 + o.buy_taker_fee_pct / 100.0)
    sell_rev = amount * o.sell_mid * (1.0 - o.sell_taker_fee_pct / 100.0)
    print(
        f"[SIM] 단순 USDT 단위: 매수≈{buy_cost:g}, 매도≈{sell_rev:g}, "
        f"잔여≈{sell_rev - buy_cost:g} | 순≈{o.net_edge_pct:+.4f}%"
    )


def _cmd_simulate_arb(
    *,
    amount: float,
    symbols: list[str] | None,
    exchanges: list[str] | None,
    min_net_pct: float | None,
    include_kimchi: bool,
    kimchi_base: str,
    kimchi_global: str | None,
    as_json: bool,
) -> int:
    eff_min = min_net_pct if min_net_pct is not None else arb_min_net_spread_pct()
    opps = _signal_payload(
        symbols=symbols,
        exchanges=exchanges,
        include_kimchi=include_kimchi,
        kimchi_base=kimchi_base,
        kimchi_global=kimchi_global,
    )
    if eff_min is not None:
        opps = [o for o in opps if o.net_edge_pct >= eff_min]
    if not opps:
        print(
            "조건에 맞는 차익 시나리오가 없습니다. "
            "거래소/심볼을 늘리거나 --min-net-pct 를 낮춰 보세요.",
            file=sys.stderr,
        )
        return 0
    if as_json:
        sims = []
        for o in opps:
            d = o.as_dict()
            d["simulate_amount_base"] = amount
            d["disclaimer"] = "시뮬레이션만, 실주문 아님"
            sims.append(d)
        print(json.dumps(sims, indent=2, ensure_ascii=False))
        return 0
    for o in opps:
        print("")
        if isinstance(o, KimchiOpportunity):
            _simulate_kimchi_human(o, amount)
        else:
            _simulate_cross_human(o, amount)
    print("\n※ 실제 체결가·수수료 등급·출금은 거래소·네트워크 상황에 따라 다릅니다.")
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
                        opp = d.get("opportunity")
                        if isinstance(opp, dict) and opp.get("net_edge_pct") is not None:
                            print(
                                f"  → 테이커 단순 순차익(추정): {float(opp['net_edge_pct']):+.4f}%"
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
        help="여러 거래소에서 동일 심볼(예: BTC/USDT) mid 가격 차이·스프레드(%%) 스캔",
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
    p_sp.add_argument(
        "--show-net",
        action="store_true",
        help="테이커 수수료(ARB_TAKER_FEE_PCT·ARB_FEE_OVERRIDES) 반영 순차익(추정) 한 줄 추가",
    )

    p_sg = sub.add_parser(
        "signals",
        help="거래소 간 차익 후보(순스프레드)만 요약. kimchi 암시 경로 옵션",
    )
    p_sg.add_argument("--symbols", help="쉼표 구분 (cross용)")
    p_sg.add_argument("--exchanges", help="쉼표 구분")
    p_sg.add_argument("--json", action="store_true", dest="signals_json")
    p_sg.add_argument("--watch", action="store_true")
    p_sg.add_argument("--interval", type=float, default=5.0)
    p_sg.add_argument(
        "--min-net-pct",
        type=float,
        default=None,
        dest="signals_min_net",
        help="순스프레드(%%) 이상만. watch 시 미지정이면 ARB_MIN_NET_SPREAD_PCT",
    )
    p_sg.add_argument(
        "--kimchi",
        action="store_true",
        help="업비트 암시 vs 해외 USDT 마켓 기회를 같은 출력에 포함",
    )
    p_sg.add_argument("--kimchi-base", default="BTC", dest="signals_kimchi_base")
    p_sg.add_argument(
        "--kimchi-global",
        default=None,
        dest="signals_kimchi_global",
        help="kimchi 비교 해외 거래소 (기본 .env ARB_GLOBAL_EXCHANGE)",
    )

    p_sm = sub.add_parser(
        "simulate-arb",
        help="차익 후보에 대해 양다리 시장가 주문을 ‘시뮬레이션’ 출력만 (실주문 없음)",
    )
    p_sm.add_argument(
        "amount",
        type=float,
        help="베이스 자산 수량 (예: BTC/USDT 에서 BTC 개수)",
    )
    p_sm.add_argument("--symbols", help="쉼표 구분")
    p_sm.add_argument("--exchanges", help="쉼표 구분")
    p_sm.add_argument("--json", action="store_true", dest="simulate_json")
    p_sm.add_argument(
        "--min-net-pct",
        type=float,
        default=None,
        dest="simulate_min_net",
        help="순스프레드(%%) 이상만. 미지정 시 ARB_MIN_NET_SPREAD_PCT (없으면 전체)",
    )
    p_sm.add_argument("--kimchi", action="store_true")
    p_sm.add_argument("--kimchi-base", default="BTC", dest="simulate_kimchi_base")
    p_sm.add_argument("--kimchi-global", default=None, dest="simulate_kimchi_global")

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
        help="(보조) USDT/법정화폐 호가 vs USD 환율로 이론가 대비 괴리(%%) — 거래소 간 차익과는 별개",
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
                show_net=args.show_net,
            )
        if args.cmd == "signals":
            return _cmd_signals(
                symbols=_parse_csv_opt(args.symbols),
                exchanges=_parse_csv_opt(args.exchanges),
                as_json=args.signals_json,
                watch=args.watch,
                interval=args.interval,
                min_net_pct=args.signals_min_net,
                include_kimchi=args.kimchi,
                kimchi_base=str(args.signals_kimchi_base).strip().upper(),
                kimchi_global=args.signals_kimchi_global,
            )
        if args.cmd == "simulate-arb":
            return _cmd_simulate_arb(
                amount=float(args.amount),
                symbols=_parse_csv_opt(args.symbols),
                exchanges=_parse_csv_opt(args.exchanges),
                min_net_pct=args.simulate_min_net,
                include_kimchi=args.kimchi,
                kimchi_base=str(args.simulate_kimchi_base).strip().upper(),
                kimchi_global=args.simulate_kimchi_global,
                as_json=args.simulate_json,
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
