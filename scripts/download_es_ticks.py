"""Download historical ES trades from Databento into the CSV format the backtester reads.

    export DATABENTO_API_KEY=...
    python scripts/download_es_ticks.py --symbol ESZ6 --start 2026-09-28 --end 2026-09-29 \
        --out data/es_2026-09-28.csv

Check the cost first with --estimate (Databento bills historical data by volume).
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import databento as db


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="ESZ6")
    p.add_argument("--start", required=True, help="e.g. 2026-09-28 or 2026-09-28T13:30")
    p.add_argument("--end", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--estimate", action="store_true", help="print the cost and exit")
    args = p.parse_args()

    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    params = dict(dataset="GLBX.MDP3", schema="trades", symbols=[args.symbol],
                  stype_in="raw_symbol", start=args.start, end=args.end)
    if args.estimate:
        print(f"Estimated cost: ${client.metadata.get_cost(**params):.2f}")
        return

    data = client.timeseries.get_range(**params)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_event", "price", "size"])
        for rec in data:
            if isinstance(rec, db.TradeMsg):
                w.writerow([rec.ts_event, f"{rec.price * 1e-9:.2f}", rec.size])
                rows += 1
    print(f"Wrote {rows} trades to {args.out}")


if __name__ == "__main__":
    main()
