"""Order block detection, modelled on the "Volumized Order Blocks | Flux Charts" logic.

How an order block forms (bullish case; bearish is the mirror image):

1. Swing highs are found with a `swing_length` lookback: bar j is a swing high
   when its high is above the highest high of the `swing_length` bars after it.
2. When a bar later *closes above* that swing high (a break of structure), the
   candle with the lowest low between the swing high and the breakout bar is
   the bullish order block. Its zone is that candle's high..low (or body only).
3. OB volume is the breakout bar plus the two bars before it. The "%" shown on
   the chart is the smaller of (older-bar volume, newer-bars volume) divided by
   the larger, which measures how balanced the push was.
4. The OB is invalidated ("becomes a breaker") when price trades through the
   far side of the zone: by wick, or by candle body when `invalidation="close"`.

This is written to match what the TradingView indicator draws, but the
settings (swing length, zone type, invalidation) must be checked against
your chart before trusting it live.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import Literal

from .models import Bar


@dataclass(slots=True)
class OrderBlock:
    id: int
    bullish: bool
    top: float
    bottom: float
    ob_bar_index: int       # the candle that defines the zone
    created_index: int      # the breakout bar that confirmed it
    volume: int
    low_volume: int
    high_volume: int
    touches: int = 0
    invalidated: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def strength_pct(self) -> float:
        hi = max(self.low_volume, self.high_volume)
        return 100.0 * min(self.low_volume, self.high_volume) / hi if hi else 0.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass(slots=True)
class _Swing:
    index: int
    price: float
    crossed: bool = False


class OrderBlockDetector:
    def __init__(
        self,
        swing_length: int = 10,
        zone: Literal["wick", "body"] = "wick",
        invalidation: Literal["wick", "close"] = "wick",
        max_active_per_side: int = 5,
        history: int = 5000,
    ):
        if swing_length < 1:
            raise ValueError("swing_length must be >= 1")
        self.swing_length = swing_length
        self.zone = zone
        self.invalidation = invalidation
        self.max_active_per_side = max_active_per_side
        self.bars: deque[Bar] = deque(maxlen=max(history, swing_length * 4))
        self.bullish: list[OrderBlock] = []
        self.bearish: list[OrderBlock] = []
        self._os: int | None = None
        self._top: _Swing | None = None
        self._btm: _Swing | None = None
        self._ids = count(1)

    @property
    def active(self) -> list[OrderBlock]:
        return self.bullish + self.bearish

    def _bar_at(self, index: int) -> Bar | None:
        if not self.bars:
            return None
        pos = index - self.bars[0].index
        if 0 <= pos < len(self.bars):
            return self.bars[pos]
        return None

    def update(self, bar: Bar) -> list[OrderBlock]:
        """Feed a closed bar. Returns any order blocks created on this bar."""
        self.bars.append(bar)
        self._invalidate(bar)
        self._update_swings()
        created: list[OrderBlock] = []

        if self._top and not self._top.crossed and bar.close > self._top.price:
            self._top.crossed = True
            ob = self._make_ob(bullish=True, swing_index=self._top.index, bar=bar)
            if ob:
                self.bullish.append(ob)
                self.bullish = self.bullish[-self.max_active_per_side:]
                created.append(ob)

        if self._btm and not self._btm.crossed and bar.close < self._btm.price:
            self._btm.crossed = True
            ob = self._make_ob(bullish=False, swing_index=self._btm.index, bar=bar)
            if ob:
                self.bearish.append(ob)
                self.bearish = self.bearish[-self.max_active_per_side:]
                created.append(ob)

        return created

    def _update_swings(self) -> None:
        n = self.swing_length
        if len(self.bars) <= n:
            return
        recent = list(self.bars)[-n:]
        candidate = self.bars[-n - 1]
        upper = max(b.high for b in recent)
        lower = min(b.low for b in recent)

        prev = self._os
        if candidate.high > upper:
            os = 0
        elif candidate.low < lower:
            os = 1
        else:
            os = prev
        self._os = os

        if os == 0 and prev != 0:
            self._top = _Swing(candidate.index, candidate.high)
        elif os == 1 and prev != 1:
            self._btm = _Swing(candidate.index, candidate.low)

    def _make_ob(self, bullish: bool, swing_index: int, bar: Bar) -> OrderBlock | None:
        # Candidates: bars strictly between the swing and the breakout bar.
        candidates = [
            b for i in range(swing_index + 1, bar.index)
            if (b := self._bar_at(i)) is not None
        ]
        if not candidates:
            prev = self._bar_at(bar.index - 1)
            if prev is None:
                return None
            candidates = [prev]

        if bullish:
            ob_bar = min(candidates, key=lambda b: (b.low, -b.index))
        else:
            ob_bar = max(candidates, key=lambda b: (b.high, b.index))

        if self.zone == "body":
            top, bottom = ob_bar.body_top, ob_bar.body_bottom
        else:
            top, bottom = ob_bar.high, ob_bar.low

        b1 = self._bar_at(bar.index - 1)
        b2 = self._bar_at(bar.index - 2)
        v0 = bar.volume
        v1 = b1.volume if b1 else 0
        v2 = b2.volume if b2 else 0
        # Bullish: older bar is the "sell" side, the breakout pair is the "buy" side.
        low_vol, high_vol = (v2, v0 + v1) if bullish else (v0 + v1, v2)

        return OrderBlock(
            id=next(self._ids),
            bullish=bullish,
            top=top,
            bottom=bottom,
            ob_bar_index=ob_bar.index,
            created_index=bar.index,
            volume=v0 + v1 + v2,
            low_volume=low_vol,
            high_volume=high_vol,
        )

    def _invalidate(self, bar: Bar) -> None:
        if self.invalidation == "close":
            lo, hi = bar.body_bottom, bar.body_top
        else:
            lo, hi = bar.low, bar.high
        for ob in self.bullish:
            if lo < ob.bottom:
                ob.invalidated = True
        for ob in self.bearish:
            if hi > ob.top:
                ob.invalidated = True
        self.bullish = [ob for ob in self.bullish if not ob.invalidated]
        self.bearish = [ob for ob in self.bearish if not ob.invalidated]
