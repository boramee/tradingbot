"""매매 전략 인터페이스"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradeSignal:
    signal: Signal
    confidence: float   # 0.0 ~ 1.0
    reason: str
    price: float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.signal != Signal.HOLD and self.confidence >= 0.5


class BaseStrategy(ABC):

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def analyze(self, df: pd.DataFrame) -> TradeSignal: ...

    @staticmethod
    def _last(df: pd.DataFrame, col: str) -> Optional[float]:
        if col in df.columns and len(df) > 0:
            v = df[col].iloc[-1]
            if pd.notna(v):
                return float(v)
        return None

    @staticmethod
    def _prev(df: pd.DataFrame, col: str, n: int = 2) -> Optional[float]:
        if col in df.columns and len(df) >= n:
            v = df[col].iloc[-n]
            if pd.notna(v):
                return float(v)
        return None
