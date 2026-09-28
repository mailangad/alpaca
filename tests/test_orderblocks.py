from spxbot.orderblocks import OrderBlockDetector

from helpers import bar, bullish_bars, mirror


def feed(det, bars):
    created = []
    for b in bars:
        created += det.update(b)
    return created


def test_bullish_ob_on_break_of_structure():
    det = OrderBlockDetector(swing_length=2)
    created = feed(det, bullish_bars())
    assert len(created) == 1
    ob = created[0]
    assert ob.bullish
    assert (ob.bottom, ob.top) == (8, 12)
    assert ob.ob_bar_index == 5 and ob.created_index == 7
    # Volume: breakout bar + previous (800 + 700) vs two bars back (600).
    assert ob.volume == 2100
    assert round(ob.strength_pct) == 40


def test_body_zone():
    det = OrderBlockDetector(swing_length=2, zone="body")
    ob = feed(det, bullish_bars())[0]
    assert (ob.bottom, ob.top) == (9, 11.5)


def test_wick_invalidation():
    det = OrderBlockDetector(swing_length=2)
    feed(det, bullish_bars())
    det.update(bar(8, 16, 16, 7.75, 15))
    assert det.bullish == []


def test_close_invalidation_ignores_wicks():
    det = OrderBlockDetector(swing_length=2, invalidation="close")
    feed(det, bullish_bars())
    det.update(bar(8, 16, 16, 7.75, 15))
    assert len(det.bullish) == 1
    det.update(bar(9, 15, 15, 7, 7.5))
    assert det.bullish == []


def test_bearish_ob_mirror():
    det = OrderBlockDetector(swing_length=2)
    created = feed(det, mirror(bullish_bars()))
    bears = [ob for ob in created if not ob.bullish]
    assert len(bears) == 1
    assert (bears[0].bottom, bears[0].top) == (8, 12)
