"""Broker interface. The engine decides *what* to trade; a broker decides *how*."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..models import Fill, Side
from ..options import OptionSpec


@dataclass(frozen=True)
class OptionOrder:
    spec: OptionSpec
    side: Side
    right: str       # "C" / "P"
    strike: float
    expiry: str      # YYYYMMDD
    contracts: int
    take_profit: float  # premium gain per share (option units) for the resting TP order

    @property
    def label(self) -> str:
        return f"{self.spec.root} {self.expiry} {self.strike:g}{self.right} x{self.contracts}"


class BrokerListener(Protocol):
    def on_entry_filled(self, fill: Fill) -> None: ...
    def on_entry_failed(self, reason: str) -> None: ...
    def on_exit_filled(self, fill: Fill) -> None: ...


class Broker(ABC):
    listener: BrokerListener | None = None

    def on_spx_price(self, spx_price: float, ts: datetime) -> None:
        """Latest SPX-equivalent price (from ES). Used to keep quotes warm."""

    def live_index_price(self) -> float | None:
        """Live SPX index price if this broker streams one."""
        return None

    @abstractmethod
    def open(self, order: OptionOrder, ts: datetime) -> None:
        """Buy to open, then rest a take-profit sell at fill + order.take_profit.

        Must eventually call on_entry_filled or on_entry_failed, and call
        on_exit_filled if the take-profit order fills.
        """

    @abstractmethod
    def close(self, reason: str, ts: datetime) -> None:
        """Cancel the take-profit and sell to close. Must call on_exit_filled."""
