"""매매 전략 기본 클래스"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from config.settings import IndicatorConfig


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradeSignal:
    signal: Signal
    confidence: float  # 0.0 ~ 1.0
    reason: str
    price: float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.signal != Signal.HOLD and self.confidence >= 0.5


class BaseStrategy(ABC):
    """매매 전략 인터페이스"""

    def __init__(self, config: IndicatorConfig):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        """데이터를 분석하여 매매 신호 반환"""
        ...

    def _latest(self, df: pd.DataFrame, column: str) -> Optional[float]:
        """최신 값 안전 조회"""
        if column in df.columns and not df[column].empty:
            val = df[column].iloc[-1]
            if pd.notna(val):
                return float(val)
        return None
