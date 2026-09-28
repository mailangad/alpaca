"""Trading rules. Everything chart-reading lives here.

Rule 1 — first touch of an order block:
  During the trading window, the FIRST time price touches an order block
  coming from the opposite side (a single tick at the zone edge counts; see
  `touch_tolerance_points` to trigger slightly before the edge):
    * green (bullish) OB touched from above  -> buy calls
    * red (bearish) OB touched from below    -> buy puts
  Entry fires on the touching tick, not on bar close.
  Take profit depends on the gamma regime (see gamma.py / engine.py).
  Stop: price trades `stop_buffer_points` beyond the far side of the OB.

Each OB gets one chance: once touched in the window it is marked used,
whether or not a trade was taken (e.g. already in a position).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import ChartConfig, OrderBlockConfig, StrategyConfig, parse_time
from .indicators import ET, EMA, AnchoredVWAP, FVGDetector
from .models import Bar, Side, Signal, Tick
from .orderblocks import OrderBlock, OrderBlockDetector


@dataclass(slots=True)
class MarketState:
    """Indicator values after the latest closed bar (for rules and logging)."""

    bar: Bar
    ema_fast: float | None
    ema_slow: float | None
    vwap: float | None
    order_blocks: list[OrderBlock]
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
            zone=obs.zone,
            invalidation=obs.invalidation,
            max_active_per_side=obs.max_active_per_side,
        )
        self.state: MarketState | None = None
        self._prev_price: float | None = None

    def in_window(self, ts: datetime) -> bool:
        t = ts.astimezone(ET).time()
        return self.window[0] <= t < self.window[1]

    def on_bar(self, bar: Bar) -> None:
        fast = self.ema_fast.update(bar.close)
        slow = self.ema_slow.update(bar.close)
        vwap = self.vwap.update(bar)
        self.fvg.update(bar)
        self.obs.update(bar)
        self.state = MarketState(bar, fast, slow, vwap, self.obs.active, self.fvg.active)

    def on_tick(self, tick: Tick) -> Signal | None:
        prev, price = self._prev_price, tick.price
        self._prev_price = price
        if prev is None or not self.in_window(tick.ts):
            return None

        r = self.rules
        tol = r.touch_tolerance_points
        signal: Signal | None = None
        for ob in self.obs.active:
            if ob.touches:
                continue
            if ob.bullish:
                # Coming down from above into the zone top.
                if not (prev > ob.top + tol >= price):
                    continue
                side, stop = Side.LONG, ob.bottom - r.stop_buffer_points
            else:
                # Coming up from below into the zone bottom.
                if not (prev < ob.bottom - tol <= price):
                    continue
                side, stop = Side.SHORT, ob.top + r.stop_buffer_points

            ob.touches += 1
            if signal is not None:
                continue  # two OBs hit on one tick: take the first, burn both
            if ob.strength_pct < r.min_ob_strength_pct:
                continue
            if not (ob.bottom - tol <= price <= ob.top + tol):
                continue  # gapped straight through the whole zone
            if r.max_risk_points and abs(price - stop) > r.max_risk_points:
                continue
            signal = Signal(
                side=side,
                es_entry=price,
                es_stop=stop,
                reason=f"first touch {'green' if ob.bullish else 'red'} OB #{ob.id} "
                f"{ob.bottom:.2f}-{ob.top:.2f} ({ob.strength_pct:.0f}%)",
                ts=tick.ts,
                meta={"ob_id": ob.id},
            )
        return signal
