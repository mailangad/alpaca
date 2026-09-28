"""Gamma exposure (GEX) regime: positive or negative dealer gamma.

Read from a small TOML file that you can edit during the day without
restarting the bot (it is re-read whenever it changes):

    # gex.toml
    regime = "auto"        # "positive", "negative", or "auto"
    flip_spx = 7715.0      # auto: SPX above the gamma flip = positive gamma

If the file is missing or unreadable the regime is "unknown", and the engine
uses the smaller negative-gamma target to stay conservative.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Regime = Literal["positive", "negative", "unknown"]


class GammaRegime:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._data: dict = {}

    def _reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._mtime, self._data = None, {}
            return
        if mtime == self._mtime:
            return
        try:
            with open(self.path, "rb") as f:
                self._data = tomllib.load(f)
            self._mtime = mtime
            log.info("Loaded GEX settings: %s", self._data)
        except (OSError, tomllib.TOMLDecodeError):
            log.exception("Could not read %s", self.path)
            self._data = {}

    def regime(self, spx_price: float) -> Regime:
        self._reload()
        mode = self._data.get("regime", "unknown")
        if mode in ("positive", "negative"):
            return mode
        if mode == "auto" and "flip_spx" in self._data:
            return "positive" if spx_price >= float(self._data["flip_spx"]) else "negative"
        return "unknown"
