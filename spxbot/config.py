"""Configuration loaded from a TOML file (see config.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import time
from pathlib import Path
from typing import Any, Literal


@dataclass
class ChartConfig:
    ticks_per_bar: int = 1000
    ema_fast: int = 9
    ema_slow: int = 21
    vwap_anchor: str = "09:30"


@dataclass
class OrderBlockConfig:
    # Same inputs as the Flux Charts indicator.
    swing_length: int = 10
    invalidation: Literal["wick", "close"] = "wick"
    zone_count: Literal["One", "Low", "Medium", "High"] = "Low"


@dataclass
class StrategyConfig:
    touch_tolerance_points: float = 0.0  # 0.25 = trigger one ES tick before the zone edge
    # Only trade big zones. 0 = no filter.
    min_zone_volume: float = 0.0         # label volume, e.g. 10000 for "10K+"
    min_zone_height_points: float = 5.0  # zone height in ES points (only 5+ point zones)
    min_ob_strength_pct: float = 0.0     # e.g. 40 to skip zones under 40%
    stop_buffer_points: float = 1.0      # ES points beyond the far side of the OB
    max_risk_points: float = 0.0         # skip OBs taller than this (0 = no limit)


@dataclass
class ExitConfig:
    # Take profit per contract, in SPX option premium dollars. XSP is 1/10 of
    # SPX, so these are scaled automatically (0.50 -> 0.05 on XSP).
    take_profit_negative_gamma: float = 0.50
    take_profit_positive_gamma: float = 2.00
    gex_file: str = "gex.toml"


@dataclass
class OptionsConfig:
    root: Literal["XSP", "SPX"] = "XSP"
    strike_offset: int = 0            # 0 = ATM, +1 = one strike OTM, -1 = one ITM
    contracts: int = 1
    # Fallback ES-minus-SPX gap when no live index price is available (dry run, backtest).
    fallback_basis_points: float = 35.0
    # Simulator only (dry run / backtest): rough option model.
    sim_delta: float = 0.5
    sim_premium_spx: float = 15.0     # starting premium in SPX terms (XSP = /10)


@dataclass
class RiskConfig:
    trade_start: str = "09:45"
    trade_end: str = "15:30"
    flatten_at: str = "15:50"
    max_trades_per_day: int = 5
    max_daily_loss_usd: float = 300.0
    max_contracts: int = 2
    kill_switch_file: str = "KILL"


@dataclass
class DatabentoConfig:
    dataset: str = "GLBX.MDP3"
    symbol: str = "ESZ6"              # roll to the next contract before expiry
    stype_in: str = "raw_symbol"


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 4002                  # IB Gateway paper = 4002, live = 4001
    client_id: int = 17
    allow_live: bool = False          # must be true to connect to a live port
    reprice_ms: int = 250
    entry_max_steps: int = 4          # mid -> ask in this many steps, then cancel
    exit_max_steps: int = 3           # mid -> bid, then fall back to a market order
    chain_width: int = 6              # strikes each side of ATM kept streaming


@dataclass
class Config:
    mode: Literal["dry_run", "ibkr"] = "dry_run"
    chart: ChartConfig = field(default_factory=ChartConfig)
    orderblocks: OrderBlockConfig = field(default_factory=OrderBlockConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)
    options: OptionsConfig = field(default_factory=OptionsConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    databento: DatabentoConfig = field(default_factory=DatabentoConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)


def parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _build(cls: type, data: dict[str, Any]):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        default = getattr(cls(), name)
        kwargs[name] = _build(type(default), value) if is_dataclass(default) else value
    return cls(**kwargs)


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    with open(path, "rb") as f:
        return _build(Config, tomllib.load(f))
