from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy
from .ma_cross import MACrossStrategy
from .combined import CombinedStrategy

STRATEGY_MAP = {
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "ma_cross": MACrossStrategy,
    "combined": CombinedStrategy,
}


def create_strategy(name: str) -> BaseStrategy:
    cls = STRATEGY_MAP.get(name.lower())
    if cls is None:
        raise ValueError(f"알 수 없는 전략: {name}. 사용 가능: {list(STRATEGY_MAP.keys())}")
    return cls()
