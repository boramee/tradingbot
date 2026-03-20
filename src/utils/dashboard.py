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

        print("=" * 95)
        print("  Crypto Arbitrage Bot - 거래소 간 재정거래 모니터링")
        print("=" * 95)
        print(
            "  가동시간: %02d:%02d:%02d | 스캔: %d회 | "
            "환율: %s KRW/USD | 거래: %d건 | 일일 PnL: %+.4f USDT"
            % (hours, minutes, seconds, self._scan_count,
               "{:,.0f}".format(fx_rate), self._trade_count, self._total_profit_usdt)
        )
        print("-" * 95)

        self._render_usdt_premium(snapshots, fx_rate)
        print()
        self._render_price_table(snapshots, fx_rate)
        print()
        self._render_opportunities(opportunities)
        print()
        self._render_kimchi_premium(snapshots)
        print("-" * 95)
        print("  Ctrl+C 종료 | 시뮬레이션 모드 (--live 옵션으로 실거래)")

    def _render_usdt_premium(self, snapshots: Dict[str, PriceSnapshot], fx_rate: float):
        """USDT(테더) 프리미엄 현황 - 핵심 지표"""
        print("\n  [USDT(테더) 거래소별 가격 현황]")

        usdt_snap = snapshots.get("USDT")
        if not usdt_snap or not usdt_snap.prices:
            print("  USDT 데이터 없음 (TARGET_SYMBOLS에 USDT 추가 필요)")
            return

        headers = ["거래소", "기준통화", "매수호가", "매도호가", "USDT환산($)", "실환율 대비 프리미엄"]
        rows = []

        for ex_name, price in sorted(usdt_snap.prices.items()):
            if price.original_quote == "KRW":
                premium = (price.bid_original - fx_rate) / fx_rate * 100 if fx_rate > 0 else 0
                rows.append([
                    ex_name.upper(),
                    "KRW",
                    "{:,.0f} won".format(price.bid_original),
                    "{:,.0f} won".format(price.ask_original),
                    "$%.4f" % price.mid_usdt,
                    "%+.2f%%" % premium,
                ])
            else:
                # 해외: USDT/USDC 실제 가격
                deviation = (price.mid_usdt - 1.0) * 100
                rows.append([
                    ex_name.upper(),
                    price.original_quote,
                    "%.4f" % price.bid_original,
                    "%.4f" % price.ask_original,
                    "$%.4f" % price.mid_usdt,
                    "%+.3f%% (vs $1)" % deviation,
                ])

        if rows:
            print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))

            korean_prices = [p for p in usdt_snap.prices.values() if p.original_quote == "KRW"]
            foreign_prices = [p for p in usdt_snap.prices.values() if p.original_quote != "KRW"]

            if korean_prices and foreign_prices:
                kr_best = max(korean_prices, key=lambda p: p.bid_original)
                fg_best = min(foreign_prices, key=lambda p: p.ask_usdt)
                kr_per_usdt = kr_best.bid_original
                fg_per_usdt = fg_best.ask_usdt * fx_rate if fx_rate > 0 else 0
                gap = kr_per_usdt - fg_per_usdt
                premium_pct = gap / fg_per_usdt * 100 if fg_per_usdt > 0 else 0

                print(
                    "\n  >> 한국 vs 해외 USDT 차익: %s원/개 (프리미엄 %+.2f%%)"
                    % ("{:,.0f}".format(gap), premium_pct)
                )
                print(
                    "     한국 최고매수: %s원 (%s) | 해외 최저매도: $%.4f (%s) = %s원"
                    % ("{:,.0f}".format(kr_per_usdt), kr_best.exchange.upper(),
                       fg_best.ask_usdt, fg_best.exchange.upper(),
                       "{:,.0f}".format(fg_per_usdt))
                )
            elif korean_prices and fx_rate > 0:
                kr_best = max(korean_prices, key=lambda p: p.bid_original)
                gap = kr_best.bid_original - fx_rate
                print(
                    "\n  >> USDT 프리미엄: %s원/개 (매수호가 %s - 실환율 %s)"
                    % ("{:,.0f}".format(gap),
                       "{:,.0f}".format(kr_best.bid_original),
                       "{:,.0f}".format(fx_rate))
                )

    def _render_price_table(self, snapshots: Dict[str, PriceSnapshot], fx_rate: float):
        """거래소별 가격 비교 테이블"""
        print("  [거래소별 실시간 가격 (USDT 기준)]")

        coin_snapshots = {s: snap for s, snap in snapshots.items() if s != "USDT"}
        if not coin_snapshots:
            print("  코인 데이터 없음")
            return

        all_exchanges = set()
        for snap in coin_snapshots.values():
            all_exchanges.update(snap.prices.keys())
        exchanges = sorted(all_exchanges)

        if not exchanges:
            print("  데이터 없음")
            return

        headers = ["코인"] + [ex.upper() for ex in exchanges] + ["최대 스프레드"]
        rows = []

        for symbol, snap in sorted(coin_snapshots.items()):
            row = [symbol]
            prices_usdt = []

            for ex in exchanges:
                p = snap.prices.get(ex)
                if p and p.mid_usdt > 0:
                    if p.original_quote == "KRW":
                        row.append(
                            "%s\n(%s won)" % ("{:,.2f}".format(p.mid_usdt),
                                               "{:,.0f}".format(p.bid_original))
                        )
                    else:
                        row.append("{:,.2f}".format(p.mid_usdt))
                    prices_usdt.append(p.mid_usdt)
                else:
                    row.append("-")

            if len(prices_usdt) >= 2:
                max_spread = (max(prices_usdt) - min(prices_usdt)) / min(prices_usdt) * 100
                row.append("%.3f%%" % max_spread)
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

        headers = ["유형", "대상", "매수 거래소", "매도 거래소", "스프레드", "순수익(예상)", "상태"]
        rows = []

        for opp in opportunities[:10]:
            status = "** 실행가능 **" if opp.is_profitable and opp.net_profit_pct >= 0.5 else "[관찰]"
            type_label = "김프" if opp.arb_type.value == "kimchi_premium" else "크로스"

            if opp.symbol == "USDT":
                buy_str = "%s(%s %s)" % (opp.buy_exchange, "{:,.0f}".format(opp.buy_price_original), opp.buy_quote)
                sell_str = "%s(%s %s)" % (opp.sell_exchange, "{:,.0f}".format(opp.sell_price_original), opp.sell_quote)
            else:
                buy_str = "%s(%s %s)" % (opp.buy_exchange, "{:,.2f}".format(opp.buy_price_original), opp.buy_quote)
                sell_str = "%s(%s %s)" % (opp.sell_exchange, "{:,.2f}".format(opp.sell_price_original), opp.sell_quote)

            rows.append([
                type_label,
                opp.symbol,
                buy_str,
                sell_str,
                "%+.3f%%" % opp.spread_pct,
                "%+.3f%%" % opp.net_profit_pct,
                status,
            ])

        print(tabulate(rows, headers=headers, tablefmt="simple"))

    def _render_kimchi_premium(self, snapshots: Dict[str, PriceSnapshot]):
        """김치프리미엄 현황 (코인별)"""
        print("  [코인별 김치프리미엄 (업비트 vs 바이낸스)]")

        headers = ["코인", "업비트(KRW)", "바이낸스(USDT)", "업비트(USDT환산)", "김치프리미엄"]
        rows = []

        for symbol, snap in sorted(snapshots.items()):
            if symbol == "USDT":
                continue

            upbit = snap.prices.get("upbit")
            binance = snap.prices.get("binance")

            if not upbit or not binance:
                continue
            if upbit.mid_usdt <= 0 or binance.mid_usdt <= 0:
                continue

            premium = (upbit.mid_usdt - binance.mid_usdt) / binance.mid_usdt * 100

            rows.append([
                symbol,
                "%s won" % "{:,.0f}".format(upbit.bid_original),
                "%s USDT" % "{:,.2f}".format(binance.mid_usdt),
                "%s USDT" % "{:,.2f}".format(upbit.mid_usdt),
                "%+.2f%%" % premium,
            ])

        if rows:
            print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))
        else:
            print("  데이터 없음 (업비트 또는 바이낸스 연결 필요)")
