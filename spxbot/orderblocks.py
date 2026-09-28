"""Order blocks: a line-by-line port of "Volumized Order Blocks | Flux Charts".

Per closed bar, in the same order as the Pine script:

1. Swings (`findOBSwings`): bar[len] is a swing high when its high is above
   the highest high of the `len` bars after it (swing low mirrored). A new
   swing high only registers after the swing state was "low" (and vice
   versa); the state starts at "high".
2. Existing bullish OBs: an OB becomes a *breaker* when price trades below its
   bottom (wick, or candle body with invalidation="close"). A breaker is
   removed once price trades back above its top.
3. New bullish OB: when a bar closes above the last swing high (first time
   only), the candle with the lowest low between the swing and the breakout
   bar (ties -> oldest) defines the zone: its high..low. Volume = breakout bar
   + 2 previous bars; "low" volume = bar[2], "high" volume = bar[0] + bar[1].
   Kept only if zone height <= ATR(10) * 3.5. Newest first, max 30 stored.
4. Bearish side is the mirror image.

Display (`handleOrderBlocksFinal`): the newest `zone_count` OBs per side
(breakers included) are shown, and any same-side zones that overlap in
time x price are merged into one box, repeatedly, until none overlap. The
merged zones are what you see on the chart, so they are what the strategy
trades.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import count
from typing import Literal

from .models import Bar

ZONE_COUNTS = {"One": 1, "Low": 3, "Medium": 5, "High": 10}
MAX_ORDER_BLOCKS = 30
ATR_LENGTH = 10


@dataclass(slots=True)
class OrderBlock:
    """A raw order block (orderBlockInfo in the Pine script)."""

    id: int
    bullish: bool
    top: float
    bottom: float
    start_time: datetime        # open time of the candle that defines the zone
    volume: float
    low_volume: float
    high_volume: float
    created_index: int          # breakout bar that confirmed it
    breaker: bool = False
    break_time: datetime | None = None


@dataclass(slots=True)
class Zone:
    """An order block as drawn on the chart (possibly several merged)."""

    bullish: bool
    top: float
    bottom: float
    start_time: datetime
    break_time: datetime | None
    breaker: bool
    volume: float
    low_volume: float
    high_volume: float
    member_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def combined(self) -> bool:
        return len(self.member_ids) > 1

    @property
    def strength_pct(self) -> int:
        """The "(48%)" label: int(min(high, low) / max(high, low) * 100)."""
        hi = max(self.high_volume, self.low_volume)
        return int(min(self.high_volume, self.low_volume) / hi * 100.0) if hi else 0

    @property
    def label(self) -> str:
        return (f"{'green' if self.bullish else 'red'} OB {self.bottom:.2f}-{self.top:.2f} "
                f"{self.volume / 1000:.3f}K ({self.strength_pct}%)")

    @classmethod
    def of(cls, ob: OrderBlock) -> "Zone":
        return cls(ob.bullish, ob.top, ob.bottom, ob.start_time, ob.break_time, ob.breaker,
                   ob.volume, ob.low_volume, ob.high_volume, frozenset({ob.id}))


@dataclass(slots=True)
class _Swing:
    index: int | None = None
    price: float | None = None
    crossed: bool = False


class _ATR:
    """ta.atr(length): RMA of true range, seeded with an SMA."""

    def __init__(self, length: int):
        self.length = length
        self.value: float | None = None
        self._seed: list[float] = []
        self._prev_close: float | None = None

    def update(self, bar: Bar) -> float | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low, abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        self._prev_close = bar.close
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) == self.length:
                self.value = sum(self._seed) / self.length
            return self.value
        self.value = (self.value * (self.length - 1) + tr) / self.length
        return self.value


def _overlap(a: Zone, b: Zone, now: datetime) -> bool:
    """doOBsTouch with overlapThresholdPercentage = 0: any positive area overlap."""
    a_end = a.break_time or now
    b_end = b.break_time or now
    dt = (min(a_end, b_end) - max(a.start_time, b.start_time)).total_seconds()
    dp = min(a.top, b.top) - max(a.bottom, b.bottom)
    return dt > 0 and dp > 0


def _merge(a: Zone, b: Zone) -> Zone:
    breaks = [t for t in (a.break_time, b.break_time) if t is not None]
    return Zone(
        bullish=a.bullish,
        top=max(a.top, b.top),
        bottom=min(a.bottom, b.bottom),
        start_time=min(a.start_time, b.start_time),
        break_time=max(breaks) if breaks else None,
        breaker=a.breaker or b.breaker,
        volume=a.volume + b.volume,
        low_volume=a.low_volume + b.low_volume,
        high_volume=a.high_volume + b.high_volume,
        member_ids=a.member_ids | b.member_ids,
    )


def combine_zones(zones: list[Zone], now: datetime) -> list[Zone]:
    """Merge overlapping same-side zones until no two overlap (combineOBsFunc)."""
    zones = list(zones)
    merged = True
    while merged:
        merged = False
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                a, b = zones[i], zones[j]
                if a.bullish == b.bullish and _overlap(a, b, now):
                    zones[i] = _merge(a, b)
                    del zones[j]
                    merged = True
                    break
            if merged:
                break
    return zones


class OrderBlockDetector:
    def __init__(
        self,
        swing_length: int = 10,
        invalidation: Literal["wick", "close"] = "wick",
        zone_count: Literal["One", "Low", "Medium", "High"] = "Low",
        combine: bool = True,
        max_atr_mult: float = 3.5,
        history: int = 5000,
    ):
        if swing_length < 1:
            raise ValueError("swing_length must be >= 1")
        self.swing_length = swing_length
        self.invalidation = invalidation
        self.visible_per_side = ZONE_COUNTS[zone_count]
        self.combine = combine
        self.max_atr_mult = max_atr_mult
        self.bars: deque[Bar] = deque(maxlen=max(history, swing_length * 4))
        self.bullish: list[OrderBlock] = []   # newest first, breakers included
        self.bearish: list[OrderBlock] = []
        self.zones: list[Zone] = []           # what the chart shows
        self._atr = _ATR(ATR_LENGTH)
        self._swing_type = 0
        self._top = _Swing()
        self._btm = _Swing()
        self._ids = count(1)

    @property
    def active_zones(self) -> list[Zone]:
        """Zones on the chart that are not breakers: the tradeable ones."""
        return [z for z in self.zones if not z.breaker]

    def _bar_at(self, index: int) -> Bar | None:
        if not self.bars:
            return None
        pos = index - self.bars[0].index
        return self.bars[pos] if 0 <= pos < len(self.bars) else None

    def update(self, bar: Bar) -> list[OrderBlock]:
        """Feed a closed bar. Returns the raw order blocks created on it."""
        self.bars.append(bar)
        atr = self._atr.update(bar)
        self._find_swings()
        created: list[OrderBlock] = []

        wick_low = bar.low if self.invalidation == "wick" else bar.body_bottom
        wick_high = bar.high if self.invalidation == "wick" else bar.body_top

        for ob in list(self.bullish):
            if not ob.breaker:
                if wick_low < ob.bottom:
                    ob.breaker, ob.break_time = True, bar.start
            elif bar.high > ob.top:
                self.bullish.remove(ob)

        top = self._top
        if top.price is not None and not top.crossed and bar.close > top.price:
            top.crossed = True
            ob = self._make_ob(True, top.index, bar)
            if ob and atr is not None and ob.top - ob.bottom <= atr * self.max_atr_mult:
                self.bullish.insert(0, ob)
                del self.bullish[MAX_ORDER_BLOCKS:]
                created.append(ob)

        for ob in list(self.bearish):
            if not ob.breaker:
                if wick_high > ob.top:
                    ob.breaker, ob.break_time = True, bar.start
            elif bar.low < ob.bottom:
                self.bearish.remove(ob)

        btm = self._btm
        if btm.price is not None and not btm.crossed and bar.close < btm.price:
            btm.crossed = True
            ob = self._make_ob(False, btm.index, bar)
            if ob and atr is not None and ob.top - ob.bottom <= atr * self.max_atr_mult:
                self.bearish.insert(0, ob)
                del self.bearish[MAX_ORDER_BLOCKS:]
                created.append(ob)

        self._render(bar)
        return created

    def _find_swings(self) -> None:
        n = self.swing_length
        if len(self.bars) <= n:
            return
        recent = list(self.bars)[-n:]
        candidate = self.bars[-n - 1]
        upper = max(b.high for b in recent)
        lower = min(b.low for b in recent)

        prev = self._swing_type
        if candidate.high > upper:
            self._swing_type = 0
        elif candidate.low < lower:
            self._swing_type = 1

        if self._swing_type == 0 and prev != 0:
            self._top = _Swing(candidate.index, candidate.high)
        if self._swing_type == 1 and prev != 1:
            self._btm = _Swing(candidate.index, candidate.low)

    def _make_ob(self, bullish: bool, swing_index: int, bar: Bar) -> OrderBlock | None:
        # Bars strictly between the swing and the breakout bar; ties go to the oldest.
        candidates = [
            b for i in range(bar.index - 1, swing_index, -1)
            if (b := self._bar_at(i)) is not None
        ]
        if not candidates:
            return None
        best = candidates[0]
        for b in candidates[1:]:
            if (b.low <= best.low) if bullish else (b.high >= best.high):
                best = b

        b1 = self._bar_at(bar.index - 1)
        b2 = self._bar_at(bar.index - 2)
        v0, v1, v2 = bar.volume, b1.volume if b1 else 0, b2.volume if b2 else 0
        low_vol, high_vol = (v2, v0 + v1) if bullish else (v0 + v1, v2)
        return OrderBlock(
            id=next(self._ids),
            bullish=bullish,
            top=best.high,
            bottom=best.low,
            start_time=best.start,
            volume=v0 + v1 + v2,
            low_volume=low_vol,
            high_volume=high_vol,
            created_index=bar.index,
        )

    def _render(self, bar: Bar) -> None:
        n = self.visible_per_side
        shown = [Zone.of(ob) for ob in self.bullish[:n] + self.bearish[:n]]
        now = bar.start + timedelta(milliseconds=1)  # Pine: time + 1
        self.zones = combine_zones(shown, now) if self.combine else shown
