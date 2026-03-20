"""테더 토큰 전용 콘솔 대시보드"""

import os
import time
from typing import Dict, List

from tabulate import tabulate

from src.monitor.price_monitor import PriceSnapshot
from src.monitor.fx_rate import TETHER_PEG, TETHER_PEG_LABEL
from src.arbitrage.detector import ArbitrageOpportunity


class Dashboard:
    """테더 토큰 모니터링 전용 대시보드"""

    def __init__(self):
        self._start_time = time.time()
        self._scan_count = 0

    def render(
        self,
        snapshots: Dict[str, PriceSnapshot],
        opportunities: List[ArbitrageOpportunity],
        daily_pnl: float = 0.0,
        trade_count: int = 0,
        fx_rate: float = 0.0,
    ):
        self._scan_count += 1

        os.system("clear" if os.name != "nt" else "cls")

        uptime = time.time() - self._start_time
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)

        print("=" * 100)
        print("  Tether Arbitrage Monitor - 테더 토큰 거래소 간 재정거래 모니터링")
        print("=" * 100)
        print(
            "  가동: %02d:%02d:%02d | 스캔: %d | "
            "거래: %d건 | 일일 PnL: %+.4f USDT"
            % (hours, minutes, seconds, self._scan_count, trade_count, daily_pnl)
        )

        self._render_fx_rates(snapshots)
        print()

        for symbol in ["USDT", "EURT", "CNHT", "XAUT"]:
            snap = snapshots.get(symbol)
            if snap and snap.prices:
                self._render_token(snap)
                print()

        self._render_opportunities(opportunities)
        print("-" * 100)
        print("  Ctrl+C 종료 | 시뮬레이션 모드 (--live 옵션으로 실거래)")

    def _render_fx_rates(self, snapshots: Dict[str, PriceSnapshot]):
        """기준 환율 표시"""
        print("-" * 100)
        parts = []
        seen = set()
        for symbol in ["USDT", "EURT", "CNHT", "XAUT"]:
            snap = snapshots.get(symbol)
            if not snap or snap.peg_currency in seen:
                continue
            seen.add(snap.peg_currency)
            peg = snap.peg_currency
            rate = snap.peg_rate_krw
            if peg == "XAU":
                parts.append("  XAU(금1oz): %s won" % "{:,.0f}".format(rate))
            else:
                parts.append("  %s/KRW: %s" % (peg, "{:,.2f}".format(rate)))
        if parts:
            print("  [기준 환율] " + " | ".join(parts))

    def _render_token(self, snap: PriceSnapshot):
        """개별 테더 토큰 가격 현황"""
        peg = snap.peg_currency
        peg_label = TETHER_PEG_LABEL.get(snap.symbol, peg)
        peg_rate = snap.peg_rate_krw

        print("  [%s] 페그: %s | 기준환율: %s KRW/%s"
              % (snap.symbol, peg_label,
                 "{:,.2f}".format(peg_rate) if peg != "XAU" else "{:,.0f}".format(peg_rate),
                 peg))

        headers = ["거래소", "거래페어", "매수호가", "매도호가", "KRW 환산", "페그 대비 가격", "프리미엄"]
        rows = []

        for ex_name, price in sorted(snap.prices.items()):
            premium = (price.price_in_peg - 1.0) * 100 if peg != "XAU" else 0

            if price.original_quote == "KRW":
                pair_str = "%s/KRW" % snap.symbol
                bid_str = "{:,.0f} won".format(price.bid_original)
                ask_str = "{:,.0f} won".format(price.ask_original)
                krw_str = "{:,.0f} won".format(price.price_in_krw)
            elif peg == "XAU":
                pair_str = "%s/%s" % (snap.symbol, price.original_quote)
                bid_str = "${:,.2f}".format(price.bid_original)
                ask_str = "${:,.2f}".format(price.ask_original)
                krw_str = "{:,.0f} won".format(price.price_in_krw)
                premium = (price.price_in_krw - peg_rate) / peg_rate * 100 if peg_rate > 0 else 0
            else:
                pair_str = "%s/%s" % (snap.symbol, price.original_quote)
                bid_str = "%.4f" % price.bid_original
                ask_str = "%.4f" % price.ask_original
                krw_str = "{:,.0f} won".format(price.price_in_krw)

            if peg == "XAU":
                peg_str = "${:,.2f}".format(price.price_in_peg) if price.price_in_peg > 100 else "%.4f" % price.price_in_peg
            else:
                peg_str = "%.4f %s" % (price.price_in_peg, peg)

            rows.append([
                ex_name.upper(),
                pair_str,
                bid_str,
                ask_str,
                krw_str,
                peg_str,
                "%+.3f%%" % premium,
            ])

        if rows:
            print(tabulate(rows, headers=headers, tablefmt="simple", stralign="right"))

            if len(snap.prices) >= 2:
                prices_krw = [(name, p.price_in_krw) for name, p in snap.prices.items()]
                prices_krw.sort(key=lambda x: x[1])
                cheapest = prices_krw[0]
                most_exp = prices_krw[-1]
                gap = most_exp[1] - cheapest[1]
                gap_pct = gap / cheapest[1] * 100 if cheapest[1] > 0 else 0
                print(
                    "  >> 최대 가격차: %s원 (%.3f%%) | "
                    "최저: %s(%s원) → 최고: %s(%s원)"
                    % ("{:,.0f}".format(gap), gap_pct,
                       cheapest[0].upper(), "{:,.0f}".format(cheapest[1]),
                       most_exp[0].upper(), "{:,.0f}".format(most_exp[1]))
                )

    def _render_opportunities(self, opportunities: List[ArbitrageOpportunity]):
        """재정거래 기회"""
        print("  [재정거래 기회 (수수료 차감 후)]")

        if not opportunities:
            print("  현재 수익성 있는 기회 없음")
            return

        headers = ["토큰", "매수 거래소", "매도 거래소", "스프레드", "순수익(예상)", "상태"]
        rows = []

        for opp in opportunities[:10]:
            status = "** 실행가능 **" if opp.net_profit_pct >= 0.5 else "[관찰]"
            rows.append([
                opp.symbol,
                "%s (%s %s)" % (opp.buy_exchange.upper(),
                                "{:,.2f}".format(opp.buy_price_original) if opp.buy_price_original < 10000 else "{:,.0f}".format(opp.buy_price_original),
                                opp.buy_quote),
                "%s (%s %s)" % (opp.sell_exchange.upper(),
                                "{:,.2f}".format(opp.sell_price_original) if opp.sell_price_original < 10000 else "{:,.0f}".format(opp.sell_price_original),
                                opp.sell_quote),
                "%+.3f%%" % opp.spread_pct,
                "%+.3f%%" % opp.net_profit_pct,
                status,
            ])

        print(tabulate(rows, headers=headers, tablefmt="simple"))
