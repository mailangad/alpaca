"""Command line entry point.

  python -m spxbot backtest --ticks data/es_2026-09-28.csv [--config config.toml]
  python -m spxbot run [--config config.toml]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .broker.sim import SimBroker
from .config import Config, load_config
from .engine import Engine
from .feeds.replay import read_ticks


def _setup_logging(verbose: bool) -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/spxbot.log")],
    )


def _sim_broker(cfg: Config) -> SimBroker:
    return SimBroker(cfg.options.sim_delta, cfg.options.sim_premium_spx)


def backtest(cfg: Config, ticks_path: str) -> Engine:
    engine = Engine(cfg, _sim_broker(cfg))
    for tick in read_ticks(ticks_path):
        engine.on_tick(tick)
    engine.flatten("end of data")
    print_summary(engine)
    return engine


def print_summary(engine: Engine) -> None:
    trades = engine.trades
    print(f"\n{'=' * 72}\nTrades: {len(trades)}")
    if not trades:
        return
    wins = [t for t in trades if t.pnl_usd > 0]
    total = sum(t.pnl_usd for t in trades)
    print(f"Win rate: {100 * len(wins) / len(trades):.0f}%   "
          f"Sim P&L: {total:+.2f} USD   ES points: {sum(t.es_points for t in trades):+.2f}")
    print(f"{'time (UTC)':<20} {'side':<6} {'ES in':>9} {'ES out':>9} {'gamma':<9} "
          f"{'exit':<12} {'P&L $':>8}")
    for t in trades:
        print(f"{t.signal.ts:%Y-%m-%d %H:%M:%S} {t.signal.side.value:<6} "
              f"{t.signal.es_entry:>9.2f} {t.es_exit or 0:>9.2f} {t.gamma:<9} "
              f"{t.exit_reason:<12} {t.pnl_usd:>8.2f}")
    print("Note: simulated option prices ignore spread, decay and IV; use for rule checks only.")


async def run_live(cfg: Config) -> None:
    from .feeds.databento_live import stream_ticks

    log = logging.getLogger("spxbot")
    ibkr = None
    if cfg.mode == "ibkr":
        from .broker.ibkr import IBKRBroker
        from .options import SPECS

        ibkr = IBKRBroker(cfg.ibkr, SPECS[cfg.options.root])
        await ibkr.connect()
        broker = ibkr
    else:
        log.info("DRY RUN: live ES data, simulated fills, no orders sent")
        broker = _sim_broker(cfg)

    engine = Engine(cfg, broker)
    try:
        async for tick in stream_ticks(cfg.databento):
            engine.on_tick(tick)
    finally:
        engine.flatten("shutdown")
        if ibkr is not None:
            await asyncio.sleep(3)  # let exit orders go out
            ibkr.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(prog="spxbot")
    parser.add_argument("--config", default=None, help="TOML config (default: built-in defaults)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    bt = sub.add_parser("backtest", help="replay an ES trades CSV through the rules")
    bt.add_argument("--ticks", required=True)
    sub.add_parser("run", help="run live on Databento ES data")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    if args.cmd == "backtest":
        backtest(cfg, args.ticks)
    else:
        try:
            asyncio.run(run_live(cfg))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
