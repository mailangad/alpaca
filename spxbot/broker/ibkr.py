"""Interactive Brokers execution through IB Gateway / TWS using ib_async.

- Keeps a window of 0DTE calls and puts around the money streaming, so an
  entry never waits for a quote subscription.
- Streams the SPX index so ES levels can be converted with the live basis.
- Entries: limit at the mid, stepped toward the ask every `reprice_ms`, then
  cancelled if still unfilled (no chasing).
- Take profit: a resting limit SELL at fill + take_profit, placed the moment
  the entry fills, so it executes at the exchange with no bot latency.
- Stop exits: cancel the take-profit, limit at the mid stepped toward the
  bid, then a market order for anything left.

Only long options are ever opened (buy to open / sell to close), so risk per
trade is capped at the premium paid.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime

from ib_async import IB, Index, LimitOrder, MarketOrder, Option, Ticker, Trade

from ..config import IBKRConfig
from ..models import Fill, Side
from ..options import OptionSpec, expiry_today, round_to_tick, select_strike
from . import Broker, OptionOrder

log = logging.getLogger(__name__)

LIVE_PORTS = {4001, 7496}


def _valid(x: float | None) -> bool:
    return x is not None and not math.isnan(x) and x > 0


class IBKRBroker(Broker):
    def __init__(self, cfg: IBKRConfig, spec: OptionSpec):
        self.cfg = cfg
        self.spec = spec
        self.ib = IB()
        self._index: Ticker | None = None
        self._chain: dict[tuple[str, str, float], tuple[Option, Ticker]] = {}
        self._warm_center: tuple[str, float] | None = None
        self._warming: asyncio.Task | None = None
        self._position: tuple[Option, Ticker, OptionOrder, int] | None = None
        self._tp: Trade | None = None
        self._busy = False
        self._closing = False

    async def connect(self) -> None:
        if self.cfg.port in LIVE_PORTS and not self.cfg.allow_live:
            raise RuntimeError(
                f"Port {self.cfg.port} is a LIVE trading port. Set ibkr.allow_live = true "
                "only when you intend to trade real money."
            )
        await self.ib.connectAsync(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id)
        self.ib.reqMarketDataType(1)
        index = Index("SPX", "CBOE", "USD")
        await self.ib.qualifyContractsAsync(index)
        self._index = self.ib.reqMktData(index)
        log.info("Connected to IBKR %s:%s, streaming SPX index", self.cfg.host, self.cfg.port)

    def disconnect(self) -> None:
        self.ib.disconnect()

    def live_index_price(self) -> float | None:
        t = self._index
        if t is None:
            return None
        for p in (t.last, t.close):
            if _valid(p):
                return p
        return None

    # ---- option chain warm-up -------------------------------------------------

    def on_spx_price(self, spx_price: float, ts: datetime) -> None:
        expiry = expiry_today(ts)
        center = select_strike(self.spec, spx_price, Side.LONG, 0)
        if self._warm_center == (expiry, center):
            return
        if self._warming and not self._warming.done():
            return
        self._warm_center = (expiry, center)
        self._warming = asyncio.get_running_loop().create_task(self._warm(expiry, center))

    def _contract(self, expiry: str, right: str, strike: float) -> Option:
        return Option(
            self.spec.root, expiry, strike, right, "SMART",
            multiplier=str(self.spec.multiplier), currency="USD",
            tradingClass=self.spec.trading_class,
        )

    async def _warm(self, expiry: str, center: float) -> None:
        inc = self.spec.strike_increment
        wanted = {
            (expiry, right, center + k * inc)
            for k in range(-self.cfg.chain_width, self.cfg.chain_width + 1)
            for right in ("C", "P")
        }
        missing = [key for key in wanted if key not in self._chain]
        if missing:
            contracts = [self._contract(*key) for key in missing]
            try:
                await self.ib.qualifyContractsAsync(*contracts)
            except Exception:  # noqa: BLE001 - keep trading on a bad strike
                log.exception("Failed to qualify option contracts")
            for key, c in zip(missing, contracts):
                if c.conId:
                    self._chain[key] = (c, self.ib.reqMktData(c))
        # Drop subscriptions far from the money (IBKR limits concurrent lines).
        keep_band = 2 * self.cfg.chain_width * inc
        held = self._position[0] if self._position else None
        for key in list(self._chain):
            if key[0] != expiry or abs(key[2] - center) > keep_band:
                contract, _ = self._chain.pop(key)
                if contract is not held:
                    self.ib.cancelMktData(contract)

    async def _quote(self, order: OptionOrder) -> tuple[Option, Ticker]:
        key = (order.expiry, order.right, order.strike)
        if key not in self._chain:
            c = self._contract(*key)
            await self.ib.qualifyContractsAsync(c)
            if not c.conId:
                raise RuntimeError(f"Unknown contract {order.label}")
            self._chain[key] = (c, self.ib.reqMktData(c))
        contract, ticker = self._chain[key]
        for _ in range(20):  # up to ~1s for a first quote
            if _valid(ticker.bid) and _valid(ticker.ask):
                break
            await asyncio.sleep(0.05)
        return contract, ticker

    # ---- orders ---------------------------------------------------------------

    def open(self, order: OptionOrder, ts: datetime) -> None:
        if self._busy or self._position:
            self.listener.on_entry_failed("broker busy or already in a position")
            return
        self._busy = True
        asyncio.get_running_loop().create_task(self._open(order))

    async def _open(self, order: OptionOrder) -> None:
        try:
            contract, ticker = await self._quote(order)
            bid, ask = ticker.bid, ticker.ask
            if not (_valid(bid) and _valid(ask) and ask >= bid):
                self.listener.on_entry_failed(f"no valid quote for {order.label}")
                return
            steps = max(1, self.cfg.entry_max_steps)
            mid = (bid + ask) / 2
            price = round_to_tick(self.spec, mid, "up")
            ib_order = LimitOrder("BUY", order.contracts, price, tif="DAY")
            trade = self.ib.placeOrder(contract, ib_order)
            log.info("BUY %s limit %.2f (bid %.2f / ask %.2f)", order.label, price, bid, ask)

            for step in range(1, steps + 1):
                if await self._wait_done(trade):
                    break
                bid, ask = ticker.bid, ticker.ask
                if not (_valid(bid) and _valid(ask)):
                    continue
                mid = (bid + ask) / 2
                new_price = round_to_tick(self.spec, mid + (ask - mid) * step / steps, "up")
                if new_price != ib_order.lmtPrice:
                    ib_order.lmtPrice = new_price
                    self.ib.placeOrder(contract, ib_order)
                    log.info("  reprice BUY -> %.2f", new_price)
            else:
                await self._wait_done(trade)

            if not trade.isDone():
                self.ib.cancelOrder(ib_order)
                await self._wait_done(trade, timeout=2.0)

            filled = int(trade.orderStatus.filled)
            if filled <= 0:
                self.listener.on_entry_failed(f"not filled at {ib_order.lmtPrice:.2f}, cancelled")
                return
            avg = trade.orderStatus.avgFillPrice
            self._position = (contract, ticker, order, filled)
            self.listener.on_entry_filled(Fill(avg, filled, datetime.now().astimezone(), order.label))
            self._place_take_profit(contract, order, filled, avg)
        except Exception as e:  # noqa: BLE001
            log.exception("Entry failed")
            self.listener.on_entry_failed(str(e))
        finally:
            self._busy = False

    def _place_take_profit(self, contract: Option, order: OptionOrder, qty: int, avg: float) -> None:
        price = round_to_tick(self.spec, avg + order.take_profit, "up")
        self._tp = self.ib.placeOrder(contract, LimitOrder("SELL", qty, price, tif="DAY"))
        self._tp.filledEvent += self._on_tp_filled
        log.info("TP resting: SELL %s @ %.2f", order.label, price)

    def _on_tp_filled(self, trade: Trade) -> None:
        if self._closing or not self._position:
            return  # a stop exit is in progress and will account for this fill
        order = self._position[2]
        self._position, self._tp = None, None
        log.info("TP FILLED %s @ %.2f", order.label, trade.orderStatus.avgFillPrice)
        self.listener.on_exit_filled(Fill(
            trade.orderStatus.avgFillPrice, int(trade.orderStatus.filled),
            datetime.now().astimezone(), order.label,
        ))

    def close(self, reason: str, ts: datetime) -> None:
        if not self._position or self._busy:
            return
        self._busy = True
        self._closing = True
        asyncio.get_running_loop().create_task(self._close(reason))

    async def _close(self, reason: str) -> None:
        contract, ticker, order, qty = self._position
        try:
            remaining = qty
            fills: list[tuple[float, int]] = []
            if self._tp is not None:
                tp = self._tp
                if not tp.isDone():
                    self.ib.cancelOrder(tp.order)
                    await self._wait_done(tp, timeout=2.0)
                tp_filled = int(tp.orderStatus.filled)
                if tp_filled:
                    fills.append((tp.orderStatus.avgFillPrice, tp_filled))
                    remaining -= tp_filled
                self._tp = None
            if remaining <= 0:
                bid = ask = None  # take-profit already covered the whole position
            else:
                bid, ask = ticker.bid, ticker.ask
            steps = max(1, self.cfg.exit_max_steps)
            if _valid(bid) and _valid(ask):
                mid = (bid + ask) / 2
                ib_order = LimitOrder("SELL", remaining, round_to_tick(self.spec, mid, "down"), tif="DAY")
                trade = self.ib.placeOrder(contract, ib_order)
                log.info("SELL %s limit %.2f (%s)", order.label, ib_order.lmtPrice, reason)
                for step in range(1, steps + 1):
                    if await self._wait_done(trade):
                        break
                    bid, ask = ticker.bid, ticker.ask
                    if not (_valid(bid) and _valid(ask)):
                        continue
                    mid = (bid + ask) / 2
                    new_price = round_to_tick(self.spec, mid - (mid - bid) * step / steps, "down")
                    if new_price != ib_order.lmtPrice:
                        ib_order.lmtPrice = new_price
                        self.ib.placeOrder(contract, ib_order)
                        log.info("  reprice SELL -> %.2f", new_price)
                if not trade.isDone():
                    self.ib.cancelOrder(ib_order)
                    await self._wait_done(trade, timeout=2.0)
                done = int(trade.orderStatus.filled)
                if done:
                    fills.append((trade.orderStatus.avgFillPrice, done))
                remaining -= done

            if remaining > 0:
                log.warning("Exit not filled by limit, sending MARKET for %d", remaining)
                trade = self.ib.placeOrder(contract, MarketOrder("SELL", remaining))
                while not await self._wait_done(trade, timeout=5.0):
                    log.warning("Market exit still working...")
                fills.append((trade.orderStatus.avgFillPrice, int(trade.orderStatus.filled)))

            total = sum(q for _, q in fills)
            avg = sum(p * q for p, q in fills) / total if total else 0.0
            self._position = None
            self.listener.on_exit_filled(Fill(avg, total, datetime.now().astimezone(), order.label))
        except Exception:  # noqa: BLE001
            log.exception("EXIT FAILED — check the position in IBKR manually")
        finally:
            self._busy = False
            self._closing = False

    async def _wait_done(self, trade: Trade, timeout: float | None = None) -> bool:
        wait = timeout if timeout is not None else self.cfg.reprice_ms / 1000
        deadline = asyncio.get_running_loop().time() + wait
        while asyncio.get_running_loop().time() < deadline:
            if trade.isDone():
                return True
            await asyncio.sleep(0.01)
        return trade.isDone()
