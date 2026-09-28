from spxbot.bars import TickBarBuilder
from spxbot.indicators import EMA, FVGDetector

from helpers import bar, tick


def test_tick_bars_close_every_n_prints():
    b = TickBarBuilder(3)
    out = [b.update(tick(p, size=2)) for p in (10, 12, 9, 11)]
    assert out[:2] == [None, None]
    done = out[2]
    assert (done.open, done.high, done.low, done.close) == (10, 12, 9, 9)
    assert done.volume == 6 and done.ticks == 3 and done.index == 0
    assert out[3] is None and b.forming.open == 11 and b.forming.index == 1


def test_ema_seeds_with_sma_like_tradingview():
    e = EMA(3)
    assert e.update(1) is None and e.update(2) is None
    assert e.update(3) == 2.0
    assert e.update(6) == 0.5 * 6 + 0.5 * 2.0


def test_fvg_detect_and_mitigate():
    f = FVGDetector()
    f.update(bar(0, 10, 11, 9, 10.5))
    f.update(bar(1, 10.5, 14, 10.5, 13.5))
    gap = f.update(bar(2, 13.5, 15, 12, 14.5))
    assert gap.bullish and (gap.bottom, gap.top) == (11, 12)
    f.update(bar(3, 14.5, 14.5, 10, 10.5))  # closes below the gap
    assert f.active == []
