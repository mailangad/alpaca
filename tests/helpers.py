from datetime import datetime, timedelta

from spxbot.indicators import ET
from spxbot.models import Bar, Tick

T0 = datetime(2026, 9, 28, 10, 0, tzinfo=ET)


def bar(i: int, o: float, h: float, l: float, c: float, v: int = 100, t0=T0) -> Bar:
    ts = t0 + timedelta(minutes=i)
    return Bar(i, ts, ts, o, h, l, c, v, 1000)


# A swing high at 15 (bar 2), a pullback to 8 (bar 5), then bar 7 closes
# above 15: break of structure -> bullish OB on bar 5 = 8.0 .. 12.0.
BULLISH_SETUP = [
    (9.5, 10, 9, 9.5),
    (10, 12, 10, 11.5),
    (11.5, 15, 11, 14),
    (14, 14, 12, 12.5),
    (12.5, 13, 11, 11.5),
    (11.5, 12, 8, 9),
    (9, 13, 9, 12.5),
    (12.5, 16.5, 12, 16),
]


def bullish_bars(t0=T0) -> list[Bar]:
    return [bar(i, *ohlc, v=100 * (i + 1), t0=t0) for i, ohlc in enumerate(BULLISH_SETUP)]


def mirror(bars: list[Bar], pivot: float = 20.0) -> list[Bar]:
    """Reflect prices around `pivot` to turn a bullish setup into a bearish one."""
    return [
        Bar(b.index, b.start, b.end, pivot - b.open, pivot - b.low, pivot - b.high,
            pivot - b.close, b.volume, b.ticks)
        for b in bars
    ]


def tick(price: float, minute: float = 30, size: int = 1, t0=T0) -> Tick:
    return Tick(t0 + timedelta(minutes=minute), price, size)
