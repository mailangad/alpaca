"""Core data types shared across the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(Enum):
    LONG = "long"    # bullish on ES -> buy calls
    SHORT = "short"  # bearish on ES -> buy puts

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(slots=True)
class Tick:
    """A single ES trade print. TradingView tick charts count one of these per tick."""

    ts: datetime  # timezone-aware
    price: float
    size: int


@dataclass(slots=True)
class Bar:
    index: int
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    ticks: int

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)


@dataclass(slots=True)
class Signal:
    side: Side
    es_entry: float
    es_stop: float
    reason: str
    ts: datetime
    meta: dict = field(default_factory=dict)

    @property
    def risk_points(self) -> float:
        return abs(self.es_entry - self.es_stop)


@dataclass(slots=True)
class Fill:
    price: float      # option premium per share (x multiplier for dollars)
    contracts: int
    ts: datetime
    symbol: str = ""
