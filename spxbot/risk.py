"""Hard limits that sit between the strategy and the broker."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from .config import RiskConfig, parse_time
from .indicators import ET

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.trade_start = parse_time(cfg.trade_start)
        self.trade_end = parse_time(cfg.trade_end)
        self.flatten_at = parse_time(cfg.flatten_at)
        self._day: date | None = None
        self.trades_today = 0
        self.realized_pnl_today = 0.0
        self.halted_reason: str | None = None

    def _roll_day(self, ts: datetime) -> None:
        day = ts.astimezone(ET).date()
        if day != self._day:
            self._day = day
            self.trades_today = 0
            self.realized_pnl_today = 0.0
            if self.halted_reason and not self.halted_reason.startswith("kill"):
                self.halted_reason = None

    def kill_switch_active(self) -> bool:
        return Path(self.cfg.kill_switch_file).exists()

    def must_flatten(self, ts: datetime) -> str | None:
        """Reason to close any open position right now, or None."""
        self._roll_day(ts)
        if self.kill_switch_active():
            self.halted_reason = "kill switch file present"
            return self.halted_reason
        if ts.astimezone(ET).time() >= self.flatten_at:
            return "end-of-day flatten"
        return None

    def check_entry(self, ts: datetime, contracts: int) -> str | None:
        """Returns a rejection reason, or None if the trade is allowed."""
        self._roll_day(ts)
        if self.kill_switch_active():
            self.halted_reason = "kill switch file present"
        if self.halted_reason:
            return f"halted: {self.halted_reason}"
        t = ts.astimezone(ET).time()
        if not (self.trade_start <= t < self.trade_end):
            return "outside trading window"
        if self.trades_today >= self.cfg.max_trades_per_day:
            return "max trades per day reached"
        if contracts > self.cfg.max_contracts:
            return f"{contracts} contracts exceeds max {self.cfg.max_contracts}"
        return None

    def record_entry(self, ts: datetime) -> None:
        self._roll_day(ts)
        self.trades_today += 1

    def record_exit(self, ts: datetime, pnl_usd: float) -> None:
        self._roll_day(ts)
        self.realized_pnl_today += pnl_usd
        if self.realized_pnl_today <= -abs(self.cfg.max_daily_loss_usd):
            self.halted_reason = (
                f"daily loss limit hit ({self.realized_pnl_today:.2f} USD)"
            )
            log.warning("Trading halted for the day: %s", self.halted_reason)
