"""콘솔 대시보드 - 실시간 가격 및 재정거래 기회 표시"""

import os
import time
from typing import Dict, List, Optional

from tabulate import tabulate

from src.monitor.price_monitor import PriceSnapshot
from src.arbitrage.detector import ArbitrageOpportunity


class Dashboard:
    """터미널 기반 실시간 모니터링 대시보드"""

    def __init__(self):
        self._start_time = time.time()
        self._scan_count = 0
        self._trade_count = 0
        self._total_profit_usdt = 0.0

    def render(
        self,
        snapshots: Dict[str, PriceSnapshot],
        opportunities: List[ArbitrageOpportunity],
        daily_pnl: float = 0.0,
        trade_count: int = 0,
        fx_rate: float = 0.0,
    ):
        """대시보드 갱신"""
        self._scan_count += 1
        self._trade_count = trade_count
        self._total_profit_usdt = daily_pnl

        os.system("clear" if os.name != "nt" else "cls")

        uptime = time.time() - self._start_time
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)

        print("=" * 90)
        print("  📊 거래소 간 재정거래 모니터링 (Crypto Arbitrage Bot)")
        print("=" * 90)
        print(f"  가동시간: {hours:02d}:{minutes:02d}:{seconds:02d} | "
              f"스캔: {self._scan_count}회 | "
              f"환율: {fx_rate:,.0f} KRW/USDT | "
              f"거래: {self._trade_count}건 | "
              f"일일 PnL: {self._total_profit_usdt:+.4f} USDT")
        print("-" * 90)

        self._render_price_table(snapshots, fx_rate)
        print()
        self._render_opportunities(opportunities)
        print()
        self._render_kimchi_premium(snapshots)
        print("-" * 90)
        print("  Ctrl+C로 종료 | 시뮬레이션 모드 (--live 옵션으로 실거래)")

    def _render_price_table(self, snapshots: Dict[str, PriceSnapshot], fx_rate: float):
        """거래소별 가격 비교 테이블"""
        print("\n  [거래소별 실시간 가격 (USDT 기준)]")

        all_exchanges = set()
        for snap in snapshots.values():
            all_exchanges.update(snap.prices.keys())
        exchanges = sorted(all_exchanges)

        if not exchanges:
            print("  데이터 없음")
            return

        headers = ["코인"] + [ex.upper() for ex in exchanges] + ["최대 스프레드"]
        rows = []

        for symbol, snap in sorted(snapshots.items()):
            row = [symbol]
            prices_usdt = []

            for ex in exchanges:
                p = snap.prices.get(ex)
                if p and p.mid_usdt > 0:
                    if p.original_quote == "KRW":
                        row.append(f"{p.mid_usdt:,.2f}\n({p.bid_original:,.0f}₩)")
                    else:
                        row.append(f"{p.mid_usdt:,.2f}")
                    prices_usdt.append(p.mid_usdt)
                else:
                    row.append("-")

            if len(prices_usdt) >= 2:
                max_spread = (max(prices_usdt) - min(prices_usdt)) / min(prices_usdt) * 100
                row.append(f"{max_spread:.3f}%")
            else:
                row.append("-")

            rows.append(row)

        print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))

    def _render_opportunities(self, opportunities: List[ArbitrageOpportunity]):
        """탐지된 재정거래 기회"""
        print("  [재정거래 기회]")

        if not opportunities:
            print("  현재 수익성 있는 기회 없음")
            return

        headers = ["유형", "코인", "매수 거래소", "매도 거래소", "스프레드", "순수익(예상)", "상태"]
        rows = []

        for opp in opportunities[:10]:
            status = "✅ 실행 가능" if opp.is_profitable and opp.net_profit_pct >= 0.5 else "⚠️ 관찰"
            type_label = "김프" if opp.arb_type.value == "kimchi_premium" else "크로스"
            rows.append([
                type_label,
                opp.symbol,
                f"{opp.buy_exchange}({opp.buy_price_original:,.2f}{opp.buy_quote})",
                f"{opp.sell_exchange}({opp.sell_price_original:,.2f}{opp.sell_quote})",
                f"{opp.spread_pct:+.3f}%",
                f"{opp.net_profit_pct:+.3f}%",
                status,
            ])

        print(tabulate(rows, headers=headers, tablefmt="simple"))

    def _render_kimchi_premium(self, snapshots: Dict[str, PriceSnapshot]):
        """김치프리미엄 현황"""
        print("  [김치프리미엄 현황 (업비트 vs 바이낸스)]")

        headers = ["코인", "업비트(KRW)", "바이낸스(USDT)", "업비트(USDT환산)", "김치프리미엄"]
        rows = []

        for symbol, snap in sorted(snapshots.items()):
            upbit = snap.prices.get("upbit")
            binance = snap.prices.get("binance")

            if not upbit or not binance:
                continue
            if upbit.mid_usdt <= 0 or binance.mid_usdt <= 0:
                continue

            premium = (upbit.mid_usdt - binance.mid_usdt) / binance.mid_usdt * 100
            sign = "+" if premium > 0 else ""

            rows.append([
                symbol,
                f"{upbit.bid_original:,.0f} ₩",
                f"{binance.mid_usdt:,.2f} USDT",
                f"{upbit.mid_usdt:,.2f} USDT",
                f"{sign}{premium:.2f}%",
            ])

        if rows:
            print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))
        else:
            print("  데이터 없음 (업비트 또는 바이낸스 연결 필요)")
