"""Wires ticks -> bars -> strategy -> risk -> broker, and manages the open trade.

Per ES tick:
  1. stop / end-of-day / kill-switch checks on the open position
  2. first-touch entry check against active order blocks
  3. bar building; on bar close, indicators and order blocks update

Take profit is a resting limit order at the broker (placed the moment the
entry fills), so it executes at the exchange with no bot latency. The stop is
tracked here on ES prices, tick by tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .bars import TickBarBuilder
from .broker import Broker, OptionOrder
from .config import Config
from .gamma import GammaRegime
from .models import Fill, Signal, Tick
from .options import SPECS, BasisTracker, expiry_today, right_for, select_strike
from .risk import RiskManager
from .strategy import OrderBlockStrategy

log = logging.getLogger(__name__)


class State(Enum):
    FLAT = "flat"
    ENTERING = "entering"
    OPEN = "open"
    EXITING = "exiting"


@dataclass
class TradeRecord:
    signal: Signal
    order: OptionOrder
    gamma: str
    entry: Fill | None = None
    exit: Fill | None = None
    es_exit: float | None = None
    exit_reason: str = ""

    @property
    def pnl_usd(self) -> float:
        if not (self.entry and self.exit):
            return 0.0
        mult = self.order.spec.multiplier
        return (self.exit.price - self.entry.price) * mult * self.exit.contracts

    @property
    def es_points(self) -> float:
        if self.es_exit is None:
            return 0.0
        return (self.es_exit - self.signal.es_entry) * self.signal.side.sign


@dataclass
class Engine:
    cfg: Config
    broker: Broker
    state: State = State.FLAT
    current: TradeRecord | None = None
    trades: list[TradeRecord] = field(default_factory=list)
    last_price: float | None = None
    last_ts: datetime | None = None

    def __post_init__(self) -> None:
        c = self.cfg
        self.spec = SPECS[c.options.root]
        self.bars = TickBarBuilder(c.chart.ticks_per_bar)
        self.strategy = OrderBlockStrategy(
            c.chart, c.orderblocks, c.strategy, c.risk.trade_start, c.risk.trade_end
        )
        self.risk = RiskManager(c.risk)
        self.basis = BasisTracker(c.options.fallback_basis_points)
        self.gamma = GammaRegime(c.exits.gex_file)
        self.broker.listener = self

    # ---- market data ----------------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        self.last_price, self.last_ts = tick.price, tick.ts
        self.basis.on_es(tick.price)
        index = self.broker.live_index_price()
        if index is not None:
            self.basis.on_index(index)
        self.broker.on_spx_price(self.basis.to_spx(tick.price), tick.ts)

        self._manage_position(tick)
        signal = self.strategy.on_tick(tick)
        if signal is not None:
            self._on_signal(signal)
        bar = self.bars.update(tick)
        if bar is not None:
            self.strategy.on_bar(bar)
            log.debug("bar #%d %s O%.2f H%.2f L%.2f C%.2f V%d", bar.index,
                      bar.end.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume)

    def _take_profit(self, regime: str) -> float:
        e = self.cfg.exits
        spx_tp = e.take_profit_positive_gamma if regime == "positive" else e.take_profit_negative_gamma
        return spx_tp * self.spec.index_scale

    def _on_signal(self, signal: Signal) -> None:
        log.info("SIGNAL %s @ %.2f stop %.2f | %s",
                 signal.side.value, signal.es_entry, signal.es_stop, signal.reason)
        if self.state is not State.FLAT:
            log.info("  skipped: already %s", self.state.value)
            return
        contracts = self.cfg.options.contracts
        reject = self.risk.check_entry(signal.ts, contracts)
        if reject:
            log.info("  skipped: %s", reject)
            return

        spx = self.basis.to_spx(signal.es_entry)
        regime = self.gamma.regime(spx)
        if regime == "unknown":
            log.warning("  GEX regime unknown (check %s); using negative-gamma target",
                        self.cfg.exits.gex_file)
        order = OptionOrder(
            spec=self.spec,
            side=signal.side,
            right=right_for(signal.side),
            strike=select_strike(self.spec, spx, signal.side, self.cfg.options.strike_offset),
            expiry=expiry_today(signal.ts),
            contracts=contracts,
            take_profit=self._take_profit(regime),
        )
        log.info("  ENTER %s | gamma %s -> TP +%.2f | SPX~%.2f (basis %.2f%s)",
                 order.label, regime, order.take_profit, spx, self.basis.basis,
                 "" if self.basis.live else " fallback")
        self.current = TradeRecord(signal, order, regime)
        self.state = State.ENTERING
        self.risk.record_entry(signal.ts)
        self.broker.open(order, signal.ts)

    def _manage_position(self, tick: Tick) -> None:
        if self.state is not State.OPEN or self.current is None:
            return
        sig = self.current.signal
        reason = self.risk.must_flatten(tick.ts)
        if reason is None and sig.side.sign * (tick.price - sig.es_stop) <= 0:
            reason = "stop"
        if reason:
            self._exit(reason, tick.price, tick.ts)

    def _exit(self, reason: str, es_price: float, ts: datetime) -> None:
        assert self.current is not None
        self.current.es_exit = es_price
        self.current.exit_reason = reason
        self.state = State.EXITING
        log.info("  EXIT (%s) ES %.2f", reason, es_price)
        self.broker.close(reason, ts)

    def flatten(self, reason: str) -> None:
        """Close any open position immediately (kill switch, shutdown)."""
        if self.state is State.OPEN and self.current and self.last_ts:
            self._exit(reason, self.last_price, self.last_ts)

    # ---- broker callbacks ------------------------------------------------------

    def on_entry_filled(self, fill: Fill) -> None:
        assert self.current is not None
        self.current.entry = fill
        self.state = State.OPEN
        log.info("  FILLED BUY %s @ %.2f (TP resting @ %.2f)",
                 fill.symbol, fill.price, fill.price + self.current.order.take_profit)

    def on_entry_failed(self, reason: str) -> None:
        log.warning("  entry failed: %s", reason)
        self.current = None
        self.state = State.FLAT

    def on_exit_filled(self, fill: Fill) -> None:
        rec = self.current
        assert rec is not None
        rec.exit = fill
        if not rec.exit_reason:
            rec.exit_reason = "take-profit"
            rec.es_exit = self.last_price
        self.trades.append(rec)
        self.risk.record_exit(self.last_ts or fill.ts, rec.pnl_usd)
        log.info("  FILLED SELL %s @ %.2f | P&L %.2f USD (%+.2f ES pts, %s) | day %.2f",
                 fill.symbol, fill.price, rec.pnl_usd, rec.es_points, rec.exit_reason,
                 self.risk.realized_pnl_today)
        self.current = None
        self.state = State.FLAT
