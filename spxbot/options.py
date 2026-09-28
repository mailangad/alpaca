"""Mapping ES signals onto XSP/SPX 0DTE option contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .indicators import ET
from .models import Side


@dataclass(frozen=True)
class OptionSpec:
    root: str            # IBKR symbol
    trading_class: str   # SPXW for daily SPX expiries
    index_scale: float   # option underlying = SPX * index_scale
    strike_increment: float
    penny_ticks: bool    # XSP trades in $0.01 for all series
    multiplier: int = 100


SPECS = {
    # XSP = SPX / 10, $1 strikes, daily expirations, $0.01 ticks.
    "XSP": OptionSpec("XSP", "XSP", 0.1, 1.0, penny_ticks=True),
    # SPX daily (0DTE) contracts trade under the SPXW class, $5 strikes.
    "SPX": OptionSpec("SPX", "SPXW", 1.0, 5.0, penny_ticks=False),
}


class BasisTracker:
    """Converts ES prices to SPX index prices.

    ES trades at a premium to SPX (interest minus dividends until expiry), so
    an ES level must be shifted by the current gap before it means anything on
    SPX. With a live SPX price the gap is measured continuously; otherwise a
    configured fallback is used.
    """

    def __init__(self, fallback_points: float):
        self.basis = fallback_points
        self.live = False
        self._last_es: float | None = None

    def on_es(self, es_price: float) -> None:
        self._last_es = es_price

    def on_index(self, spx_price: float) -> None:
        if self._last_es is not None and spx_price > 0 and not math.isnan(spx_price):
            self.basis = self._last_es - spx_price
            self.live = True

    def to_spx(self, es_price: float) -> float:
        return es_price - self.basis


def select_strike(spec: OptionSpec, spx_price: float, side: Side, offset: int) -> float:
    """ATM strike on the option's underlying, shifted `offset` strikes out of the money."""
    underlying = spx_price * spec.index_scale
    atm = round(underlying / spec.strike_increment) * spec.strike_increment
    # OTM for calls is higher strikes, for puts lower.
    return atm + side.sign * offset * spec.strike_increment


def right_for(side: Side) -> str:
    return "C" if side is Side.LONG else "P"


def expiry_today(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%Y%m%d")


def price_increment(spec: OptionSpec, price: float) -> float:
    """XSP: $0.01. SPX: $0.05 below $3.00, $0.10 at or above."""
    if spec.penny_ticks:
        return 0.01
    return 0.05 if price < 3.0 else 0.10


def round_to_tick(spec: OptionSpec, price: float, direction: str = "nearest") -> float:
    inc = price_increment(spec, price)
    steps = price / inc
    if direction == "up":
        steps = math.ceil(steps - 1e-9)
    elif direction == "down":
        steps = math.floor(steps + 1e-9)
    else:
        steps = round(steps)
    return max(inc, round(steps * inc, 2))
