"""Streaming indicators computed on closed bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from zoneinfo import ZoneInfo

from .models import Bar

ET = ZoneInfo("America/New_York")


class EMA:
    """Matches TradingView's ta.ema: seeded with an SMA of the first `length` values."""

    def __init__(self, length: int):
        self.length = length
        self.alpha = 2.0 / (length + 1)
        self.value: float | None = None
        self._seed: list[float] = []

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.length:
                self.value = sum(self._seed) / self.length
                self._seed.clear()
            return self.value
        self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value


class AnchoredVWAP:
    """VWAP of hlc3 that resets each day at `anchor` (ET), e.g. 09:30 for the RTH session."""

    def __init__(self, anchor: time = time(9, 30)):
        self.anchor = anchor
        self.value: float | None = None
        self._session: date | None = None
        self._pv = 0.0
        self._vol = 0

    def _session_key(self, bar: Bar) -> date | None:
        local = bar.end.astimezone(ET)
        if local.time() < self.anchor:
            return None  # before today's anchor: no VWAP yet for this session
        return local.date()

    def update(self, bar: Bar) -> float | None:
        key = self._session_key(bar)
        if key is None:
            self.value = None
            return None
        if key != self._session:
            self._session = key
            self._pv = 0.0
            self._vol = 0
        hlc3 = (bar.high + bar.low + bar.close) / 3
        self._pv += hlc3 * bar.volume
        self._vol += bar.volume
        self.value = self._pv / self._vol if self._vol else None
        return self.value


@dataclass(slots=True)
class FVG:
    bullish: bool
    top: float
    bottom: float
    created_index: int
    mitigated: bool = False


class FVGDetector:
    """Three-bar fair value gaps.

    Bullish FVG: bar[i].low > bar[i-2].high (gap between them).
    Bearish FVG: bar[i].high < bar[i-2].low.
    A gap is mitigated when a later bar closes back through its far edge.
    """

    def __init__(self, max_active: int = 10):
        self.max_active = max_active
        self.active: list[FVG] = []
        self._window: list[Bar] = []

    def update(self, bar: Bar) -> FVG | None:
        for gap in self.active:
            if gap.bullish and bar.close < gap.bottom:
                gap.mitigated = True
            elif not gap.bullish and bar.close > gap.top:
                gap.mitigated = True
        self.active = [g for g in self.active if not g.mitigated]

        self._window.append(bar)
        if len(self._window) > 3:
            self._window.pop(0)
        if len(self._window) < 3:
            return None

        first, _, last = self._window
        new: FVG | None = None
        if last.low > first.high:
            new = FVG(True, top=last.low, bottom=first.high, created_index=last.index)
        elif last.high < first.low:
            new = FVG(False, top=first.low, bottom=last.high, created_index=last.index)
        if new:
            self.active.append(new)
            self.active = self.active[-self.max_active:]
        return new
