"""Live ES trade prints from Databento (CME Globex MDP 3.0)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import databento as db

from ..config import DatabentoConfig
from ..models import Tick

log = logging.getLogger(__name__)

PRICE_SCALE = 1e-9  # Databento prices are fixed-point integers


async def stream_ticks(cfg: DatabentoConfig) -> AsyncIterator[Tick]:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("Set the DATABENTO_API_KEY environment variable")
    client = db.Live(key=key, reconnect_policy="reconnect")
    client.subscribe(
        dataset=cfg.dataset,
        schema="trades",
        symbols=[cfg.symbol],
        stype_in=cfg.stype_in,
    )
    log.info("Subscribed to Databento %s trades for %s", cfg.dataset, cfg.symbol)
    client.start()
    try:
        async for record in client:
            if isinstance(record, db.TradeMsg):
                yield Tick(
                    ts=datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc),
                    price=record.price * PRICE_SCALE,
                    size=record.size,
                )
            elif isinstance(record, db.ErrorMsg):
                log.error("Databento error: %s", record.err)
    finally:
        client.stop()
