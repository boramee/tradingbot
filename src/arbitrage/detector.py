"""재정거래(아비트라지) 기회 탐지 엔진"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from config.settings import ArbitrageConfig
from src.monitor.price_monitor import NormalizedPrice, PriceSnapshot

logger = logging.getLogger(__name__)


class ArbitrageType(Enum):
    KIMCHI_PREMIUM = "kimchi_premium"   # 한국 거래소 vs 해외 거래소
    CROSS_EXCHANGE = "cross_exchange"   # 해외 거래소 간 가격 차이


@dataclass
class ArbitrageOpportunity:
    """탐지된 재정거래 기회"""
    arb_type: ArbitrageType
    symbol: str

    buy_exchange: str    # 싸게 살 수 있는 거래소
    sell_exchange: str   # 비싸게 팔 수 있는 거래소

    buy_price_usdt: float    # USDT 기준 매수가 (ask)
    sell_price_usdt: float   # USDT 기준 매도가 (bid)

    buy_price_original: float
    sell_price_original: float
    buy_quote: str
    sell_quote: str

    spread_pct: float        # 가격 차이 (%)
    net_profit_pct: float    # 수수료 차감 후 순수익률 (%)

    buy_volume: float = 0.0
    sell_volume: float = 0.0
    fx_rate: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def is_profitable(self) -> bool:
        return self.net_profit_pct > 0

    def summary(self) -> str:
        direction = "+" if self.net_profit_pct > 0 else ""
        return (
            f"[{self.arb_type.value}] {self.symbol} | "
            f"매수:{self.buy_exchange}({self.buy_price_original:,.2f} {self.buy_quote}) → "
            f"매도:{self.sell_exchange}({self.sell_price_original:,.2f} {self.sell_quote}) | "
            f"스프레드: {self.spread_pct:+.3f}% | "
            f"순수익: {direction}{self.net_profit_pct:.3f}%"
        )


class ArbitrageDetector:
    """멀티 거래소 가격 스냅샷에서 재정거래 기회를 탐지"""

    def __init__(self, config: ArbitrageConfig, fee_rates: Optional[Dict[str, float]] = None):
        self.config = config
        self.fee_rates = fee_rates or {}

    def detect_all(self, snapshots: Dict[str, PriceSnapshot]) -> List[ArbitrageOpportunity]:
        """모든 심볼에 대해 재정거래 기회 탐지"""
        opportunities: List[ArbitrageOpportunity] = []

        for symbol, snapshot in snapshots.items():
            if snapshot.exchange_count < 2:
                continue

            opps = self._find_opportunities(snapshot)
            opportunities.extend(opps)

        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        return opportunities

    def detect_profitable(self, snapshots: Dict[str, PriceSnapshot]) -> List[ArbitrageOpportunity]:
        """최소 수익률 이상인 기회만 반환"""
        all_opps = self.detect_all(snapshots)
        return [o for o in all_opps if o.net_profit_pct >= self.config.min_profit_pct]

    def _find_opportunities(self, snapshot: PriceSnapshot) -> List[ArbitrageOpportunity]:
        """하나의 심볼에 대해 모든 거래소 페어를 비교"""
        opportunities = []
        exchanges = list(snapshot.prices.keys())

        for i in range(len(exchanges)):
            for j in range(len(exchanges)):
                if i == j:
                    continue

                buy_ex = exchanges[i]
                sell_ex = exchanges[j]
                buy_price = snapshot.prices[buy_ex]
                sell_price = snapshot.prices[sell_ex]

                opp = self._evaluate_pair(
                    snapshot.symbol, buy_price, sell_price, snapshot.fx_rate
                )
                if opp:
                    opportunities.append(opp)

        return opportunities

    def _evaluate_pair(
        self,
        symbol: str,
        buy_side: NormalizedPrice,
        sell_side: NormalizedPrice,
        fx_rate: float,
    ) -> Optional[ArbitrageOpportunity]:
        """
        두 거래소 간 재정거래 가능성 평가.

        핵심 로직:
          - buy_side.ask_usdt: 매수 거래소에서 살 수 있는 가격 (매도호가)
          - sell_side.bid_usdt: 매도 거래소에서 팔 수 있는 가격 (매수호가)
          - 스프레드 = (sell_bid - buy_ask) / buy_ask * 100
        """
        if buy_side.ask_usdt <= 0 or sell_side.bid_usdt <= 0:
            return None

        spread_pct = (sell_side.bid_usdt - buy_side.ask_usdt) / buy_side.ask_usdt * 100

        buy_fee = self.fee_rates.get(buy_side.exchange, 0.001)
        sell_fee = self.fee_rates.get(sell_side.exchange, 0.001)
        total_fee_pct = (buy_fee + sell_fee) * 100

        net_profit_pct = spread_pct - total_fee_pct

        is_korean_buy = buy_side.original_quote == "KRW"
        is_korean_sell = sell_side.original_quote == "KRW"
        is_kimchi = (is_korean_buy != is_korean_sell)

        arb_type = ArbitrageType.KIMCHI_PREMIUM if is_kimchi else ArbitrageType.CROSS_EXCHANGE

        return ArbitrageOpportunity(
            arb_type=arb_type,
            symbol=symbol,
            buy_exchange=buy_side.exchange,
            sell_exchange=sell_side.exchange,
            buy_price_usdt=buy_side.ask_usdt,
            sell_price_usdt=sell_side.bid_usdt,
            buy_price_original=buy_side.ask_original,
            sell_price_original=sell_side.bid_original,
            buy_quote=buy_side.original_quote,
            sell_quote=sell_side.original_quote,
            spread_pct=spread_pct,
            net_profit_pct=net_profit_pct,
            buy_volume=buy_side.volume_24h,
            sell_volume=sell_side.volume_24h,
            fx_rate=fx_rate,
        )

    def calculate_kimchi_premium(
        self,
        snapshot: PriceSnapshot,
        korean_exchange: str = "upbit",
        foreign_exchange: str = "binance",
    ) -> Optional[float]:
        """김치프리미엄(%) 계산: (한국가 - 해외가) / 해외가 * 100"""
        kr = snapshot.prices.get(korean_exchange)
        foreign = snapshot.prices.get(foreign_exchange)

        if not kr or not foreign:
            return None
        if kr.mid_usdt <= 0 or foreign.mid_usdt <= 0:
            return None

        return (kr.mid_usdt - foreign.mid_usdt) / foreign.mid_usdt * 100
