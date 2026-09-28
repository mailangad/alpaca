"""Instant-fill simulated broker for dry runs and backtests.

Option prices are a rough model: a starting premium that moves by
`delta` x the underlying move. No time decay, spread or IV changes, so treat
its P&L as a sanity check on the rules, not a forecast.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import Fill
from . import Broker, OptionOrder

log = logging.getLogger(__name__)


class SimBroker(Broker):
    def __init__(self, delta: float = 0.5, premium_spx: float = 15.0):
        self.delta = delta
        self.premium_spx = premium_spx
        self._spx: float | None = None
        self._order: OptionOrder | None = None
        self._entry_spx: float | None = None
        self._entry_premium: float | None = None

    def _premium(self) -> float:
        assert self._order and self._entry_spx is not None and self._spx is not None
        scale = self._order.spec.index_scale
        move = (self._spx - self._entry_spx) * self._order.side.sign
        return max(0.01, round((self.premium_spx + self.delta * move) * scale, 2))

    def on_spx_price(self, spx_price: float, ts: datetime) -> None:
        self._spx = spx_price
        if self._order and self._entry_premium is not None:
            tp = round(self._entry_premium + self._order.take_profit, 2)
            if self._premium() >= tp:
                self._fill_exit(tp, ts, "take-profit")

    def open(self, order: OptionOrder, ts: datetime) -> None:
        if self._spx is None:
            self.listener.on_entry_failed("no price yet")
            return
        self._order = order
        self._entry_spx = self._spx
        self._entry_premium = self._premium()
        log.info("[SIM] BUY %s @ %.2f, TP rests @ %.2f", order.label, self._entry_premium,
                 self._entry_premium + order.take_profit)
        self.listener.on_entry_filled(Fill(self._entry_premium, order.contracts, ts, order.label))

    def close(self, reason: str, ts: datetime) -> None:
        if self._order:
            self._fill_exit(self._premium(), ts, reason)

    def _fill_exit(self, price: float, ts: datetime, reason: str) -> None:
        order = self._order
        self._order = self._entry_premium = self._entry_spx = None
        log.info("[SIM] SELL %s @ %.2f (%s)", order.label, price, reason)
        self.listener.on_exit_filled(Fill(price, order.contracts, ts, order.label))
