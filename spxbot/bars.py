"""Builds N-tick bars (e.g. the 1000T chart) from raw ES trade prints."""

from __future__ import annotations

from .models import Bar, Tick


class TickBarBuilder:
    """Aggregates every `ticks_per_bar` trade prints into one OHLCV bar.

    Like TradingView's tick charts, a "tick" is one trade print regardless of
    its size; volume is the sum of contracts traded.
    """

    def __init__(self, ticks_per_bar: int = 1000):
        if ticks_per_bar < 1:
            raise ValueError("ticks_per_bar must be >= 1")
        self.ticks_per_bar = ticks_per_bar
        self._next_index = 0
        self._current: Bar | None = None

    @property
    def forming(self) -> Bar | None:
        """The bar currently being built (not yet closed)."""
        return self._current

    def update(self, tick: Tick) -> Bar | None:
        """Add a tick. Returns the completed bar when this tick closes one."""
        bar = self._current
        if bar is None:
            bar = Bar(
                index=self._next_index,
                start=tick.ts,
                end=tick.ts,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=0,
                ticks=0,
            )
            self._current = bar
            self._next_index += 1

        bar.end = tick.ts
        bar.high = max(bar.high, tick.price)
        bar.low = min(bar.low, tick.price)
        bar.close = tick.price
        bar.volume += tick.size
        bar.ticks += 1

        if bar.ticks >= self.ticks_per_bar:
            self._current = None
            return bar
        return None
