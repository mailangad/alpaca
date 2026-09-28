from datetime import datetime

import pytest

from spxbot.broker.sim import SimBroker
from spxbot.config import Config
from spxbot.engine import Engine, State
from spxbot.gamma import GammaRegime
from spxbot.indicators import ET
from spxbot.models import Side
from spxbot.options import SPECS, round_to_tick, select_strike
from spxbot.risk import RiskManager

from helpers import bullish_bars, mirror, tick


def make_cfg(tmp_path, gex: str | None = None) -> Config:
    cfg = Config()
    cfg.orderblocks.swing_length = 3
    cfg.strategy.min_zone_height_points = 0  # the test zone is only 2 points tall
    cfg.risk.kill_switch_file = str(tmp_path / "KILL")
    cfg.exits.gex_file = str(tmp_path / "gex.toml")
    if gex:
        (tmp_path / "gex.toml").write_text(gex)
    return cfg


def make_engine(cfg, bars=None) -> Engine:
    engine = Engine(cfg, SimBroker(delta=0.5, premium_spx=15.0))
    for b in bars or bullish_bars():
        engine.strategy.on_bar(b)
    return engine


# ---- rule 1: first touch ---------------------------------------------------------


def test_first_touch_of_green_ob_from_above_buys_calls(tmp_path):
    engine = make_engine(make_cfg(tmp_path))
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))  # touches the zone top (101.0) from above
    assert engine.state is State.OPEN
    sig = engine.current.signal
    assert sig.side is Side.LONG and sig.es_stop == 98.0  # 99.0 bottom - 1.0 buffer
    assert engine.current.order.right == "C"


def test_second_touch_is_ignored(tmp_path):
    engine = make_engine(make_cfg(tmp_path))
    for p in (102, 101, 97.5):  # touch, then stop out
        engine.on_tick(tick(p))
    assert engine.state is State.FLAT and engine.trades[0].exit_reason == "stop"
    for p in (102, 101):
        engine.on_tick(tick(p))
    assert len(engine.trades) == 1 and engine.state is State.FLAT


def test_touch_from_below_does_not_count(tmp_path):
    engine = make_engine(make_cfg(tmp_path))
    engine.on_tick(tick(100))  # already inside the zone
    engine.on_tick(tick(101))
    assert engine.state is State.FLAT


def test_touch_outside_trading_hours_does_not_use_up_the_ob(tmp_path):
    engine = make_engine(make_cfg(tmp_path))
    engine.on_tick(tick(102, minute=-40))   # 09:20 ET, before the window
    engine.on_tick(tick(101, minute=-39))
    assert engine.state is State.FLAT
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.state is State.OPEN


def test_small_zones_are_ignored(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.strategy.min_zone_volume = 1000  # this zone is 900
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.state is State.FLAT
    cfg.strategy.min_zone_volume = 900
    cfg.strategy.min_zone_height_points = 5.0  # default "big" filter; this zone is 2.0 tall
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.state is State.FLAT
    cfg.strategy.min_zone_height_points = 2.0
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.state is State.OPEN


def test_big_zone_default_is_5_points():
    assert Config().strategy.min_zone_height_points == 5.0


def test_touch_tolerance_triggers_one_tick_early(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.strategy.touch_tolerance_points = 0.25
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101.25))
    assert engine.state is State.OPEN


def test_red_ob_touched_from_below_buys_puts(tmp_path):
    engine = make_engine(make_cfg(tmp_path), bars=mirror(bullish_bars()))
    ob = engine.strategy.obs.active_zones[0]
    engine.on_tick(tick(ob.bottom - 1))
    engine.on_tick(tick(ob.bottom))
    assert engine.state is State.OPEN
    assert engine.current.signal.side is Side.SHORT
    assert engine.current.order.right == "P"
    assert engine.current.signal.es_stop == ob.top + 1.0


# ---- gamma-based take profit --------------------------------------------------------


@pytest.mark.parametrize(
    "gex, regime, tp",
    [
        ('regime = "negative"', "negative", 0.05),   # $0.50 SPX -> $0.05 XSP
        ('regime = "positive"', "positive", 0.20),   # $2.00 SPX -> $0.20 XSP
        (None, "unknown", 0.05),                     # no file: conservative target
    ],
)
def test_take_profit_depends_on_gamma(tmp_path, gex, regime, tp):
    engine = make_engine(make_cfg(tmp_path, gex))
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.current.gamma == regime
    assert engine.current.order.take_profit == pytest.approx(tp)


def test_take_profit_fills_in_sim(tmp_path):
    engine = make_engine(make_cfg(tmp_path, 'regime = "negative"'))
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    engine.on_tick(tick(102.25))  # +1.25 pts * 0.5 delta / 10 = +0.06 on XSP
    assert engine.state is State.FLAT
    t = engine.trades[0]
    assert t.exit_reason == "take-profit"
    assert t.pnl_usd == pytest.approx(5.0)  # 0.05 x 100


def test_spx_take_profit_is_not_scaled(tmp_path):
    cfg = make_cfg(tmp_path, 'regime = "positive"')
    cfg.options.root = "SPX"
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.current.order.take_profit == pytest.approx(2.0)
    assert engine.current.order.spec.trading_class == "SPXW"


def test_gamma_auto_uses_flip_level(tmp_path):
    (tmp_path / "gex.toml").write_text('regime = "auto"\nflip_spx = 7700')
    g = GammaRegime(tmp_path / "gex.toml")
    assert g.regime(7710) == "positive"
    assert g.regime(7690) == "negative"


# ---- risk --------------------------------------------------------------------------


def test_kill_switch_blocks_entries(tmp_path):
    cfg = make_cfg(tmp_path)
    (tmp_path / "KILL").touch()
    engine = make_engine(cfg)
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    assert engine.state is State.FLAT


def test_daily_loss_limit_halts_trading(tmp_path):
    cfg = make_cfg(tmp_path)
    risk = RiskManager(cfg.risk)
    ts = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    assert risk.check_entry(ts, 1) is None
    risk.record_exit(ts, -cfg.risk.max_daily_loss_usd)
    assert "daily loss" in risk.check_entry(ts, 1)
    assert risk.check_entry(datetime(2026, 9, 29, 10, 0, tzinfo=ET), 1) is None


def test_end_of_day_flatten(tmp_path):
    engine = make_engine(make_cfg(tmp_path))
    engine.on_tick(tick(102))
    engine.on_tick(tick(101))
    engine.on_tick(tick(101.5, minute=351))  # 15:51 ET
    assert engine.state is State.FLAT
    assert engine.trades[0].exit_reason == "end-of-day flatten"


# ---- options mapping -------------------------------------------------------------------


def test_strike_selection():
    xsp, spx = SPECS["XSP"], SPECS["SPX"]
    assert select_strike(xsp, 7723.4, Side.LONG, 0) == 772
    assert select_strike(xsp, 7723.4, Side.LONG, 1) == 773
    assert select_strike(xsp, 7723.4, Side.SHORT, 1) == 771
    assert select_strike(spx, 7723.4, Side.LONG, 0) == 7725
    assert select_strike(spx, 7723.4, Side.SHORT, 2) == 7715


def test_tick_rounding():
    assert round_to_tick(SPECS["XSP"], 1.234, "up") == 1.24
    assert round_to_tick(SPECS["SPX"], 2.92, "up") == 2.95
    assert round_to_tick(SPECS["SPX"], 12.34, "down") == 12.3
