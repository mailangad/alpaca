from datetime import datetime, timedelta

from spxbot.indicators import ET
from spxbot.models import Bar, Tick

T0 = datetime(2026, 9, 28, 10, 0, tzinfo=ET)


def bar(i: int, o: float, h: float, l: float, c: float, v: int = 100, t0=T0) -> Bar:
    ts = t0 + timedelta(minutes=i)
    return Bar(i, ts, ts, o, h, l, c, v, 1000)


FLAT = (100, 100.5, 99.5, 100)

# swing_length = 3. Twelve flat bars seed ATR(10); bar 12 dips to 98 (swing
# low, needed first); bar 16 spikes to 103 (swing high); bar 20 is the lowest
# candle of the pullback (99-101); bar 22 closes at 103.5 above the swing
# high -> bullish OB on bar 20: zone 99.00-101.00.
BULLISH_SETUP = [FLAT] * 12 + [
    (100, 100.5, 98, 99.5),     # 12 swing low
    FLAT, FLAT, FLAT,           # 13-15
    (100, 103, 99.5, 102.5),    # 16 swing high
    (102.5, 102.5, 101, 101.5),  # 17
    (101.5, 102, 100.5, 101),   # 18
    (101, 101.5, 100.2, 100.5),  # 19
    (100.5, 101, 99, 99.5),     # 20 the order block candle
    (99.5, 101.5, 99.2, 101),   # 21
    (101, 103.8, 100.8, 103.5),  # 22 break of structure
]
VOLUMES = {20: 300, 21: 200, 22: 400}
OB_BOTTOM, OB_TOP = 99.0, 101.0


def bullish_bars(t0=T0) -> list[Bar]:
    return [bar(i, *ohlc, v=VOLUMES.get(i, 100), t0=t0) for i, ohlc in enumerate(BULLISH_SETUP)]


def mirror(bars: list[Bar], pivot: float = 200.0) -> list[Bar]:
    """Reflect prices around `pivot` to turn a bullish setup into a bearish one."""
    return [
        Bar(b.index, b.start, b.end, pivot - b.open, pivot - b.low, pivot - b.high,
            pivot - b.close, b.volume, b.ticks)
        for b in bars
    ]


def tick(price: float, minute: float = 30, size: int = 1, t0=T0) -> Tick:
    return Tick(t0 + timedelta(minutes=minute), price, size)
