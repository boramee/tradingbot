from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tradingbot.config import Settings
from tradingbot.exchange_client import build_exchange


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
    except Exception as e:  # noqa: BLE001 — CLI 에서 사용자에게 메시지 전달
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
