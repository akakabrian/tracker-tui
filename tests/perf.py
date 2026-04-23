"""Perf baseline for tracker-tui hot paths.

Run with `.venv/bin/python -m tests.perf`. Measures:

  - pattern_view.render_line — central UI draw
  - AudioEngine._fill_block   — mixer dominating audio thread
  - Song.empty() + mod_round_trip — cold-start bottleneck

Numbers reported in microseconds. Re-run after any change to the
render / mix path to see the delta.
"""

from __future__ import annotations

import asyncio
import time
import tempfile
from pathlib import Path

import numpy as np

from tracker_tui.app import TrackerApp
from tracker_tui.mod_io import load_mod, save_mod
from tracker_tui.song import Cell, Song
from tracker_tui.synth import AudioEngine


def _bench(label: str, fn, iters: int) -> None:
    # warm-up
    for _ in range(5):
        fn()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    dt = time.perf_counter() - t0
    us = dt * 1e6 / iters
    print(f"  {label:<38} {us:8.2f} µs/iter  ({iters} iters, {dt*1000:.1f} ms total)")


async def main() -> None:
    print("tracker-tui performance baseline")
    print()

    # --- engine / IO --------------------------------------------------
    _bench("Song.empty(4, 4)", lambda: Song.empty(4, 4), 500)

    s = Song.empty()
    s.patterns[0].rows[0][0] = Cell(note=60, instrument=1)
    s.order = [0]
    tmp = Path(tempfile.NamedTemporaryFile(suffix=".mod", delete=False).name)
    _bench("save_mod (trivial song)", lambda: save_mod(s, tmp), 200)
    _bench("load_mod (trivial song)", lambda: load_mod(tmp), 200)

    # --- synth mix ----------------------------------------------------
    e = AudioEngine(s, sound=False)
    # trigger on all 4 channels
    for ch in range(4):
        e.trigger_preview(ch, 60 + ch * 3, 1)

    def mix1k():
        left = np.zeros(1024, dtype=np.float32)
        right = np.zeros_like(left)
        e._fill_block(left, right)

    _bench("mix 1024 frames (4 oscillators)", mix1k, 2000)

    def mix512_sample():
        s2 = Song.empty()
        s2.instruments[1].waveform = "sample"
        s2.instruments[1].sample = [float(i % 10) / 10.0 for i in range(8820)]
        e2 = AudioEngine(s2, sound=False)
        e2.trigger_preview(0, 60, 1)
        left = np.zeros(512, dtype=np.float32)
        right = np.zeros_like(left)
        e2._fill_block(left, right)

    _bench("sample voice (one channel, 512 frames)", mix512_sample, 500)

    # --- render -------------------------------------------------------
    app = TrackerApp(sound=False)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        def render():
            app.pattern_view.render_line(10)
        _bench("PatternView.render_line(y=10)", render, 500)

        # cursor move = full widget refresh
        def move():
            app.action_cursor(1, 0, 0)
            app.action_cursor(-1, 0, 0)
        _bench("cursor down + up (refresh path)", move, 200)

        # channel strip refresh
        def strip():
            app.channel_strip.refresh_panel()
        _bench("ChannelStrip.refresh_panel", strip, 500)


if __name__ == "__main__":
    asyncio.run(main())
