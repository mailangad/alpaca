"""Trading rules. Everything chart-reading lives here.

Rule 1 — first touch of a big order block:
  During the trading window, the FIRST time price touches an order block
  zone (as drawn on the chart, merged zones included) coming from the
  opposite side (a single tick at the zone edge counts; see
  `touch_tolerance_points` to trigger slightly before the edge):
    * green (bullish) zone touched from above  -> buy calls
    * red (bearish) zone touched from below    -> buy puts
  Only zones passing the size filters (volume / height) are traded.
  Entry fires on the touching tick, not on bar close.
  Take profit depends on the gamma regime (see gamma.py / engine.py).
  Stop: price trades `stop_buffer_points` beyond the far side of the zone.

Each zone gets one chance: once touched in the window, the order blocks it
is made of are marked used, whether or not a trade was taken. A merged zone
counts as used if any order block inside it was already touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import ChartConfig, OrderBlockConfig, StrategyConfig, parse_time
from .indicators import ET, EMA, AnchoredVWAP, FVGDetector
from .models import Bar, Side, Signal, Tick
from .orderblocks import OrderBlockDetector, Zone


@dataclass(slots=True)
class MarketState:
    """Indicator values after the latest closed bar (for rules and logging)."""

    bar: Bar
    ema_fast: float | None
    ema_slow: float | None
    vwap: float | None
    zones: list[Zone]
    fvgs: list


class OrderBlockStrategy:
    def __init__(
        self,
        chart: ChartConfig,
        obs: OrderBlockConfig,
        rules: StrategyConfig,
        trade_start: str,
        trade_end: str,
    ):
        self.rules = rules
        self.window = (parse_time(trade_start), parse_time(trade_end))
        self.ema_fast = EMA(chart.ema_fast)
        self.ema_slow = EMA(chart.ema_slow)
        self.vwap = AnchoredVWAP(parse_time(chart.vwap_anchor))
        self.fvg = FVGDetector()
        self.obs = OrderBlockDetector(
            swing_length=obs.swing_length,
            invalidation=obs.invalidation,
            zone_count=obs.zone_count,
        )
        self.state: MarketState | None = None
        self.touched_ids: set[int] = set()
        self._prev_price: float | None = None

    def in_window(self, ts: datetime) -> bool:
        t = ts.astimezone(ET).time()
        return self.window[0] <= t < self.window[1]

    def is_big(self, zone: Zone) -> bool:
        r = self.rules
        return (
            zone.volume >= r.min_zone_volume
            and zone.top - zone.bottom >= r.min_zone_height_points
            and zone.strength_pct >= r.min_ob_strength_pct
        )

    def on_bar(self, bar: Bar) -> None:
        fast = self.ema_fast.update(bar.close)
        slow = self.ema_slow.update(bar.close)
        vwap = self.vwap.update(bar)
        self.fvg.update(bar)
        self.obs.update(bar)
        self.state = MarketState(bar, fast, slow, vwap, self.obs.zones, self.fvg.active)

    def on_tick(self, tick: Tick) -> Signal | None:
        prev, price = self._prev_price, tick.price
        self._prev_price = price
        if prev is None or not self.in_window(tick.ts):
            return None

        r = self.rules
        tol = r.touch_tolerance_points
        signal: Signal | None = None
        for zone in self.obs.active_zones:
            if zone.member_ids & self.touched_ids:
                continue
            if zone.bullish:
                # Coming down from above into the zone top.
                if not (prev > zone.top + tol >= price):
                    continue
                side, stop = Side.LONG, zone.bottom - r.stop_buffer_points
            else:
                # Coming up from below into the zone bottom.
                if not (prev < zone.bottom - tol <= price):
                    continue
                side, stop = Side.SHORT, zone.top + r.stop_buffer_points

            self.touched_ids |= zone.member_ids
            if signal is not None:
                continue  # two zones hit on one tick: take the first, burn both
            if not self.is_big(zone):
                continue
            if not (zone.bottom - tol <= price <= zone.top + tol):
                continue  # gapped straight through the whole zone
            if r.max_risk_points and abs(price - stop) > r.max_risk_points:
                continue
            signal = Signal(
                side=side,
                es_entry=price,
                es_stop=stop,
                reason=f"first touch {zone.label}" + (" [combined]" if zone.combined else ""),
                ts=tick.ts,
                meta={"ob_ids": sorted(zone.member_ids)},
            )
        return signal
