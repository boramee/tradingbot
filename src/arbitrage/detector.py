"""테더 토큰 재정거래 기회 탐지 엔진"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from config.settings import ArbitrageConfig
from src.monitor.price_monitor import NormalizedPrice, PriceSnapshot

logger = logging.getLogger(__name__)


class ArbitrageType(Enum):
    KIMCHI_PREMIUM = "kimchi_premium"
    CROSS_EXCHANGE = "cross_exchange"


@dataclass
class ArbitrageOpportunity:
    """탐지된 재정거래 기회"""
    arb_type: ArbitrageType
    symbol: str

    buy_exchange: str
    sell_exchange: str

    buy_price_usdt: float
    sell_price_usdt: float

    buy_price_original: float
    sell_price_original: float
    buy_quote: str
    sell_quote: str

    spread_pct: float
    net_profit_pct: float

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
            "[%s] %s | "
            "매수:%s(%s %s) -> "
            "매도:%s(%s %s) | "
            "스프레드: %+.3f%% | "
            "순수익: %s%.3f%%"
            % (self.arb_type.value, self.symbol,
               self.buy_exchange,
               "{:,.2f}".format(self.buy_price_original) if self.buy_price_original < 10000 else "{:,.0f}".format(self.buy_price_original),
               self.buy_quote,
               self.sell_exchange,
               "{:,.2f}".format(self.sell_price_original) if self.sell_price_original < 10000 else "{:,.0f}".format(self.sell_price_original),
               self.sell_quote,
               self.spread_pct, direction, self.net_profit_pct)
        )


class ArbitrageDetector:
    """테더 토큰의 거래소 간 가격 차이로 재정거래 기회 탐지"""

    def __init__(self, config: ArbitrageConfig, fee_rates: Optional[Dict[str, float]] = None):
        self.config = config
        self.fee_rates = fee_rates or {}

    def detect_all(self, snapshots: Dict[str, PriceSnapshot]) -> List[ArbitrageOpportunity]:
        opportunities = []
        for symbol, snapshot in snapshots.items():
            if snapshot.exchange_count < 2:
                continue
            opps = self._find_opportunities(snapshot)
            opportunities.extend(opps)
        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        return opportunities

    def detect_profitable(self, snapshots: Dict[str, PriceSnapshot]) -> List[ArbitrageOpportunity]:
        return [o for o in self.detect_all(snapshots) if o.net_profit_pct >= self.config.min_profit_pct]

    def _find_opportunities(self, snapshot: PriceSnapshot) -> List[ArbitrageOpportunity]:
        opportunities = []
        exchanges = list(snapshot.prices.keys())

        for i in range(len(exchanges)):
            for j in range(len(exchanges)):
                if i == j:
                    continue
                buy_ex = exchanges[i]
                sell_ex = exchanges[j]
                opp = self._evaluate_pair(
                    snapshot.symbol,
                    snapshot.prices[buy_ex],
                    snapshot.prices[sell_ex],
                    snapshot.peg_rate_krw,
                )
                if opp:
                    opportunities.append(opp)

        return opportunities

    def _evaluate_pair(
        self,
        symbol: str,
        buy_side: NormalizedPrice,
        sell_side: NormalizedPrice,
        peg_rate: float,
    ) -> Optional[ArbitrageOpportunity]:
        """KRW 기준으로 두 거래소 간 가격차 평가"""
        buy_krw = buy_side.price_in_krw
        sell_krw = sell_side.price_in_krw
        if buy_krw <= 0 or sell_krw <= 0:
            return None

        spread_pct = (sell_krw - buy_krw) / buy_krw * 100

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
            buy_price_usdt=buy_side.price_in_peg,
            sell_price_usdt=sell_side.price_in_peg,
            buy_price_original=buy_side.ask_original,
            sell_price_original=sell_side.bid_original,
            buy_quote=buy_side.original_quote,
            sell_quote=sell_side.original_quote,
            spread_pct=spread_pct,
            net_profit_pct=net_profit_pct,
            buy_volume=buy_side.volume_24h,
            sell_volume=sell_side.volume_24h,
            fx_rate=peg_rate,
        )

    def calculate_premium(self, snapshot: PriceSnapshot, ex_a: str, ex_b: str) -> Optional[float]:
        """두 거래소 간 프리미엄(%) 계산"""
        a = snapshot.prices.get(ex_a)
        b = snapshot.prices.get(ex_b)
        if not a or not b or a.price_in_krw <= 0 or b.price_in_krw <= 0:
            return None
        return (a.price_in_krw - b.price_in_krw) / b.price_in_krw * 100
