from datetime import timedelta

from spxbot.orderblocks import OrderBlock, OrderBlockDetector, Zone, combine_zones

from helpers import OB_BOTTOM, OB_TOP, T0, bar, bullish_bars, mirror


def feed(det, bars):
    created = []
    for b in bars:
        created += det.update(b)
    return created


def test_bullish_ob_on_break_of_structure():
    det = OrderBlockDetector(swing_length=3)
    created = feed(det, bullish_bars())
    assert len(created) == 1
    ob = created[0]
    assert ob.bullish and (ob.bottom, ob.top) == (OB_BOTTOM, OB_TOP)
    assert ob.start_time == T0 + timedelta(minutes=20)
    assert (ob.volume, ob.low_volume, ob.high_volume) == (900, 300, 600)
    zone = det.active_zones[0]
    assert zone.strength_pct == 50 and not zone.combined


def test_swing_high_needs_a_swing_low_first():
    # Same data without the bar-12 dip: the state starts at "high", so the
    # spike at bar 16 is never registered as a new swing high.
    bars = bullish_bars()
    bars[12] = bar(12, 100, 100.5, 99.5, 100)
    det = OrderBlockDetector(swing_length=3)
    assert feed(det, bars) == []


def test_ob_taller_than_3_5_atr_is_dropped():
    det = OrderBlockDetector(swing_length=3, max_atr_mult=0.5)
    assert feed(det, bullish_bars()) == []


def test_ties_pick_the_oldest_candle():
    bars = bullish_bars()
    bars[21] = bar(21, 99.5, 101.2, 99, 101, v=200)  # same low as bar 20
    det = OrderBlockDetector(swing_length=3)
    ob = feed(det, bars)[0]
    assert ob.start_time == T0 + timedelta(minutes=20) and ob.top == 101


def test_wick_breaker_then_removed():
    det = OrderBlockDetector(swing_length=3)
    feed(det, bullish_bars())
    det.update(bar(23, 103, 103, 98.75, 100))  # trades below 99 -> breaker
    assert det.bullish[0].breaker
    assert det.active_zones == [] and det.zones[0].breaker  # still drawn
    det.update(bar(24, 100, 101.5, 100, 101.25))  # back above 101 -> removed
    assert det.bullish == []


def test_close_invalidation_ignores_wicks():
    det = OrderBlockDetector(swing_length=3, invalidation="close")
    feed(det, bullish_bars())
    det.update(bar(23, 103, 103, 98.75, 100))
    assert len(det.active_zones) == 1
    det.update(bar(24, 100, 100, 98, 98.5))
    assert det.active_zones == []


def test_bearish_ob_mirror():
    det = OrderBlockDetector(swing_length=3)
    created = feed(det, mirror(bullish_bars()))
    assert len(created) == 1 and not created[0].bullish
    assert (created[0].bottom, created[0].top) == (200 - OB_TOP, 200 - OB_BOTTOM)
    assert (created[0].low_volume, created[0].high_volume) == (600, 300)


def _ob(i, top, bottom, minute, bullish=True, breaker_at=None):
    return OrderBlock(i, bullish, top, bottom, T0 + timedelta(minutes=minute), 1000, 400, 600, 0,
                      breaker=breaker_at is not None,
                      break_time=None if breaker_at is None else T0 + timedelta(minutes=breaker_at))


def test_overlapping_zones_merge_like_the_chart():
    now = T0 + timedelta(minutes=60)
    zones = [Zone.of(_ob(1, 105, 100, 0)), Zone.of(_ob(2, 102, 98, 10)),
             Zone.of(_ob(3, 95, 90, 5)), Zone.of(_ob(4, 104, 99, 1, bullish=False))]
    out = combine_zones(zones, now)
    bulls = sorted((z for z in out if z.bullish), key=lambda z: z.top)
    assert [(z.bottom, z.top) for z in bulls] == [(90, 95), (98, 105)]
    merged = bulls[1]
    assert merged.combined and merged.volume == 2000 and merged.member_ids == {1, 2}
    assert len([z for z in out if not z.bullish]) == 1  # bearish never merges with bullish


def test_zone_broken_before_other_starts_does_not_merge():
    now = T0 + timedelta(minutes=60)
    out = combine_zones([Zone.of(_ob(1, 105, 100, 0, breaker_at=5)),
                         Zone.of(_ob(2, 104, 101, 10))], now)
    assert len(out) == 2


def test_merge_with_breaker_becomes_breaker():
    now = T0 + timedelta(minutes=60)
    out = combine_zones([Zone.of(_ob(1, 105, 100, 0, breaker_at=30)),
                         Zone.of(_ob(2, 104, 101, 10))], now)
    assert len(out) == 1 and out[0].breaker


def test_only_newest_three_per_side_are_shown():
    det = OrderBlockDetector(swing_length=3, zone_count="Low")
    det.bullish = [_ob(i, 10 * i + 5, 10 * i, i) for i in range(5, 0, -1)]  # newest first
    det._render(bar(60, 0, 0, 0, 0))
    assert {z.top for z in det.zones} == {55, 45, 35}
