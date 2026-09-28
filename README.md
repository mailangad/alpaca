# spxbot

Reads live ES futures ticks, finds order blocks on a 1000-tick chart, and trades
XSP (later SPX) 0DTE options through Interactive Brokers.

```
Databento (CME ES ticks) ─► engine (1000T bars, order blocks, rules, risk) ─► IBKR ─► Cboe
```

## Rules implemented

**Rule 1 — first touch of a big order block**

- During the trading window (default 09:45–15:30 ET), the first time price
  touches an order block from the opposite side:
  - green (bullish) OB, price coming down into it → **buy calls**
  - red (bearish) OB, price coming up into it → **buy puts**
- Only big zones are traded: set `min_zone_volume` / `min_zone_height_points`.
- Entry fires on the touching tick (a single tick at the zone edge counts).
- Each OB gets one chance; touches outside trading hours don't use it up.
- **Take profit** depends on gamma exposure, read from `gex.toml`:
  - negative gamma → +$0.50 on the SPX option (+$0.05 on XSP)
  - positive gamma → +$2.00 on the SPX option (+$0.20 on XSP)
  - unknown → uses the smaller negative-gamma target
- The take profit is a resting limit order placed the moment the entry fills,
  so it executes at the exchange with no bot delay.
- **Stop:** price trades `stop_buffer_points` beyond the far side of the OB
  (placeholder until the real stop rule is decided).

Order block detection is a port of the Flux Charts "Volumized Order Blocks"
Pine script (`spxbot/orderblocks.py`): same swing detection, ATR(10) x 3.5 size
filter, breaker handling, "Zone Count" display limit, and merging of
overlapping zones. The bot trades the zones as they appear on the chart.

## Safety

- `mode = "dry_run"` by default: live data, simulated fills, no orders sent.
- Connecting to a live IBKR port is refused unless `ibkr.allow_live = true`.
- Only long options are opened, so risk per trade is capped at the premium paid.
- Max trades per day, max daily loss, max contracts, flat by 15:50 ET.
- Kill switch: create a file named `KILL` in the working directory to flatten
  the position and block new trades.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
cp gex.example.toml gex.toml
pytest
```

### Backtest

```bash
export DATABENTO_API_KEY=...
python scripts/download_es_ticks.py --start 2026-09-28 --end 2026-09-29 --out data/es.csv --estimate
python scripts/download_es_ticks.py --start 2026-09-28 --end 2026-09-29 --out data/es.csv
python -m spxbot --config config.toml backtest --ticks data/es.csv
```

Backtest option prices are a rough delta model (no spread, decay or IV).
Use them to check that the rules fire where you expect, not to forecast P&L.

### Live

1. Install and log in to IB Gateway (paper account), with the API enabled on port 4002.
2. Subscribe to IBKR's OPRA (options) and Cboe index market data.
3. `export DATABENTO_API_KEY=...`
4. `python -m spxbot --config config.toml run`. Start with `mode = "dry_run"`,
   then switch to `mode = "ibkr"` on the paper account.

Logs go to `logs/spxbot.log`.

## Layout

| File | What it does |
|---|---|
| `spxbot/bars.py` | Builds N-tick bars from trade prints |
| `spxbot/orderblocks.py` | Order block detection and invalidation |
| `spxbot/indicators.py` | EMA, anchored VWAP, fair value gaps |
| `spxbot/strategy.py` | Trading rules |
| `spxbot/gamma.py` | Gamma regime from `gex.toml` |
| `spxbot/engine.py` | Ticks → rules → risk → broker; manages stops |
| `spxbot/risk.py` | Daily limits, trading window, kill switch |
| `spxbot/options.py` | ES → SPX basis, strike selection, tick sizes |
| `spxbot/broker/ibkr.py` | IBKR execution (entries, resting TP, exits) |
| `spxbot/broker/sim.py` | Simulated fills for dry runs and backtests |
| `spxbot/feeds/` | Databento live feed and CSV replay |
