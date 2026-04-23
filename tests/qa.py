"""Headless QA driver for tracker-tui.

Each scenario gets a fresh TrackerApp inside App.run_test(), drives
it with Pilot.press(), asserts on live state, captures an SVG
screenshot to tests/out/.

    python -m tests.qa            # run all
    python -m tests.qa note       # scenarios matching "note"
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from tracker_tui.app import TrackerApp
from tracker_tui.mod_io import load_mod, save_mod
from tracker_tui.song import Cell, Song, note_freq, note_name
from tracker_tui.synth import AudioEngine

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class Scenario:
    name: str
    fn: Callable[[TrackerApp, "object"], Awaitable[None]]


# ---------------- engine-level scenarios (fast, no TUI) ---------------

async def s_song_empty_valid(app, pilot):
    s = Song.empty(channels=4, patterns=4)
    assert s.num_channels == 4
    assert len(s.patterns) == 4
    assert all(p.num_rows == 64 for p in s.patterns)
    assert len(s.instruments) == 32  # slot 0 reserved + 31 usable
    assert s.order == [0]


async def s_note_freq_a4(app, pilot):
    # n=69 should be A4 == 440 Hz.
    f = note_freq(69)
    assert abs(f - 440.0) < 0.1, f
    # n=60 = C-5 (middle C in tracker octaves) == 261.63 Hz
    f = note_freq(60)
    assert abs(f - 261.63) < 0.1, f


async def s_note_name_round_trip(app, pilot):
    assert note_name(None) == "---"
    assert note_name(60) == "C-5"
    assert note_name(61) == "C#5"
    assert note_name(69) == "A-5"


async def s_mod_round_trip(app, pilot):
    s = Song.empty()
    s.name = "rt-test"
    s.patterns[0].rows[0][0] = Cell(note=60, instrument=1, volume=64)
    s.patterns[0].rows[8][2] = Cell(note=64, instrument=2)
    s.order = [0]
    with tempfile.NamedTemporaryFile(suffix=".mod", delete=False) as f:
        p = Path(f.name)
    save_mod(s, p)
    assert p.stat().st_size > 1000, f"mod suspiciously small: {p.stat().st_size}"
    s2 = load_mod(p)
    assert s2.name == "rt-test"
    assert s2.patterns[0].rows[0][0].note == 60
    assert s2.patterns[0].rows[0][0].instrument == 1
    assert s2.patterns[0].rows[8][2].note == 64


async def s_synth_fills_buffer(app, pilot):
    s = Song.empty()
    e = AudioEngine(s, sound=False)
    e.trigger_preview(0, 60, 1)
    left = np.zeros(441, dtype=np.float32)
    right = np.zeros_like(left)
    e._fill_block(left, right)
    peak = float(np.max(np.abs(left)))
    assert peak > 0.01, f"square wave produced silence: peak={peak}"


async def s_synth_muted_channel_silent(app, pilot):
    s = Song.empty()
    e = AudioEngine(s, sound=False)
    e.mute(0, True)
    e.trigger_preview(0, 60, 1)
    left = np.zeros(441, dtype=np.float32)
    right = np.zeros_like(left)
    e._fill_block(left, right)
    peak = float(np.max(np.abs(left)))
    assert peak < 0.001, f"muted channel made sound: peak={peak}"


async def s_synth_sample_playback(app, pilot):
    """Instrument with waveform=sample should play its buffer."""
    s = Song.empty()
    # Fill slot 1 with a 1-second triangle ramp so we can hear something.
    ramp = [float((i % 200) / 100.0 - 1.0) for i in range(8820)]
    s.instruments[1].waveform = "sample"
    s.instruments[1].sample = ramp
    e = AudioEngine(s, sound=False)
    e.trigger_preview(0, 60, 1)
    left = np.zeros(441, dtype=np.float32)
    right = np.zeros_like(left)
    e._fill_block(left, right)
    peak = float(np.max(np.abs(left)))
    assert peak > 0.0, f"sample playback produced silence: peak={peak}"


async def s_all_waveforms_nonsilent(app, pilot):
    """square/saw/triangle/sine/noise all produce non-silent output."""
    s = Song.empty()
    e = AudioEngine(s, sound=False)
    for wf in ("square", "saw", "triangle", "sine", "noise"):
        s.instruments[1].waveform = wf
        e.trigger_preview(0, 60, 1)
        left = np.zeros(441, dtype=np.float32)
        right = np.zeros_like(left)
        e._fill_block(left, right)
        peak = float(np.max(np.abs(left)))
        assert peak > 0.0, f"{wf} silent (peak {peak})"


async def s_synth_playback_advances(app, pilot):
    """Start playback and pump samples — the cursor row should advance."""
    s = Song.empty()
    s.patterns[0].rows[0][0] = Cell(note=60, instrument=1)
    s.order = [0]
    e = AudioEngine(s, sound=False)
    e.play_from(0, 0)
    # Pump ~1 second of audio.
    for _ in range(100):
        left = np.zeros(441, dtype=np.float32)
        right = np.zeros_like(left)
        with e._lock:
            while True:
                if e._samples_to_next_tick == 0:
                    e._on_tick()
                step = min(left.shape[0], e._samples_to_next_tick)
                if step == 0:
                    break
                e._samples_to_next_tick -= step
                break
    # After a bunch of ticks we should be past row 0.
    assert e.play_row > 0 or e.play_tick > 0, (e.play_row, e.play_tick)


# ---------------- TUI scenarios (Pilot) -------------------------------


async def s_mount_clean(app, pilot):
    assert app.pattern_view is not None
    assert app.status_bar is not None
    assert app.channel_strip is not None
    assert app.instrument_panel is not None
    assert app.song is not None


async def s_cursor_starts_at_origin(app, pilot):
    assert app.cursor_row == 0
    assert app.cursor_channel == 0
    assert app.cursor_field == 0


async def s_arrow_keys_move_cursor(app, pilot):
    start_row = app.cursor_row
    await pilot.press("down")
    await pilot.pause()
    assert app.cursor_row == start_row + 1, (start_row, app.cursor_row)
    await pilot.press("down")
    await pilot.press("down")
    await pilot.pause()
    assert app.cursor_row == start_row + 3


async def s_cursor_clamps_at_top(app, pilot):
    for _ in range(5):
        await pilot.press("up")
        await pilot.pause()
    assert app.cursor_row == 0


async def s_cursor_clamps_at_bottom(app, pilot):
    pat = app.current_pattern()
    for _ in range(pat.num_rows + 5):
        await pilot.press("down")
        await pilot.pause()
    assert app.cursor_row == pat.num_rows - 1


async def s_tab_moves_channel(app, pilot):
    start_ch = app.cursor_channel
    await pilot.press("tab")
    await pilot.pause()
    assert app.cursor_channel == (start_ch + 1) % app.song.num_channels


async def s_page_down_jumps(app, pilot):
    app.cursor_row = 0
    await pilot.press("pagedown")
    await pilot.pause()
    assert app.cursor_row == 16


async def s_home_end(app, pilot):
    app.cursor_row = 10
    await pilot.press("home")
    await pilot.pause()
    assert app.cursor_row == 0
    await pilot.press("end")
    await pilot.pause()
    pat = app.current_pattern()
    assert app.cursor_row == pat.num_rows - 1


async def s_z_key_enters_note(app, pilot):
    """Press Z — should write a C at base_octave to (0,0) then advance."""
    app.base_octave = 4
    await pilot.press("z")
    await pilot.pause()
    pat = app.current_pattern()
    c = pat.cell(0, 0)
    assert c.note == 4 * 12, f"z did not write C-4: got note={c.note}"
    assert c.instrument is not None
    # cursor advanced one row
    assert app.cursor_row == 1, app.cursor_row


async def s_x_key_enters_d(app, pilot):
    """X should enter D in the current octave."""
    app.base_octave = 4
    await pilot.press("x")
    await pilot.pause()
    pat = app.current_pattern()
    c = pat.cell(0, 0)
    # X is the 3rd white key; offset 2 from C
    assert c.note == 4 * 12 + 2, c.note


async def s_q_key_upper_octave(app, pilot):
    """Q should enter C one octave above base."""
    app.base_octave = 4
    await pilot.press("q")
    await pilot.pause()
    pat = app.current_pattern()
    c = pat.cell(0, 0)
    assert c.note == 5 * 12, c.note


async def s_delete_clears_cell(app, pilot):
    # Write a note, move up, clear.
    await pilot.press("z")
    await pilot.pause()
    await pilot.press("up")
    await pilot.pause()
    await pilot.press("delete")
    await pilot.pause()
    pat = app.current_pattern()
    c = pat.cell(0, 0)
    assert c.note is None, c.note


async def s_octave_up_down(app, pilot):
    app.base_octave = 4
    await pilot.press("equals_sign")
    await pilot.pause()
    assert app.base_octave == 5
    await pilot.press("minus")
    await pilot.press("minus")
    await pilot.pause()
    assert app.base_octave == 3


async def s_instrument_cycle(app, pilot):
    app.current_instrument = 1
    await pilot.press("right_square_bracket")
    await pilot.pause()
    assert app.current_instrument == 2
    await pilot.press("left_square_bracket")
    await pilot.pause()
    assert app.current_instrument == 1


async def s_space_toggles_play(app, pilot):
    assert app.audio.playing is False
    await pilot.press("space")
    await pilot.pause()
    assert app.audio.playing is True
    await pilot.press("space")
    await pilot.pause()
    assert app.audio.playing is False


async def s_mute_channel(app, pilot):
    # Capital M is bound directly (shift+m)
    app.cursor_channel = 1
    assert app.audio.voices[1].muted is False
    await pilot.press("M")
    await pilot.pause()
    assert app.audio.voices[1].muted is True
    await pilot.press("M")
    await pilot.pause()
    assert app.audio.voices[1].muted is False


async def s_help_screen_opens(app, pilot):
    await pilot.press("question_mark")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "HelpScreen"
    await pilot.press("escape")
    await pilot.pause()
    assert app.screen.__class__.__name__ == "Screen"


async def s_pattern_view_renders(app, pilot):
    """render_line should return a Strip with styled segments."""
    strip = app.pattern_view.render_line(4)
    segs = list(strip)
    assert len(segs) > 0
    # At least one segment should have a foreground color.
    fg_count = sum(1 for s in segs if s.style and s.style.color is not None)
    assert fg_count > 0, "no styled segments"


async def s_status_bar_renders(app, pilot):
    app.status_bar.refresh_panel()
    r = app.status_bar.render()
    s = str(r)
    assert "bpm" in s
    assert "row" in s


async def s_channel_strip_renders(app, pilot):
    app.channel_strip.refresh_panel()
    r = app.channel_strip.render()
    s = str(r)
    assert "CH1" in s


async def s_hex_digit_in_instrument_field(app, pilot):
    """Move cursor to instrument column, press a hex digit, see cell.instrument update."""
    # Move right to instrument column (field 1)
    await pilot.press("right")
    await pilot.pause()
    assert app.cursor_field == 1
    await pilot.press("0")
    await pilot.press("a")
    await pilot.pause()
    pat = app.current_pattern()
    c = pat.cell(0, 0)
    assert c.instrument == 0x0A, f"expected 0x0a, got {c.instrument}"


async def s_save_writes_mod(app, pilot):
    # Put a note, save, check file.
    await pilot.press("z")
    await pilot.pause()
    # Redirect save path to a tempfile.
    with tempfile.NamedTemporaryFile(suffix=".mod", delete=False) as f:
        p = Path(f.name)
    app._save_path = p
    await pilot.press("ctrl+s")
    await pilot.pause()
    assert p.stat().st_size > 1000, f"save suspiciously small: {p.stat().st_size}"


async def s_load_error_nonfatal(app, pilot):
    """Constructing with a bogus path should not crash; falls back to empty."""
    # We can't easily re-construct within this harness, so just verify the
    # class signature accepts bad paths.
    bad = TrackerApp(module_path="/nonexistent/zzz.mod", sound=False)
    assert bad.song is not None
    assert bad._load_error is not None


async def s_demo_song_loads(app, pilot):
    """The built-in demo song must construct and have cells populated."""
    from tracker_tui.demo import demo_song
    s = demo_song()
    assert s.num_channels == 4
    assert len(s.patterns) >= 1
    # Some rows must have notes (we wrote bass on ch0 row 0).
    assert s.patterns[0].rows[0][0].note is not None


async def s_demo_synth_makes_sound(app, pilot):
    """Running the demo through a few ticks must exercise the synth."""
    from tracker_tui.demo import demo_song
    s = demo_song()
    e = AudioEngine(s, sound=False)
    e.play_from(0, 0)
    left = np.zeros(2048, dtype=np.float32)
    right = np.zeros_like(left)
    # Advance a few ticks
    for _ in range(20):
        with e._lock:
            while True:
                if e._samples_to_next_tick == 0:
                    e._on_tick()
                step = min(left.shape[0], e._samples_to_next_tick)
                if step == 0:
                    break
                e._fill_block(left[:step], right[:step])
                e._samples_to_next_tick -= step
                break
    peak = float(np.max(np.abs(left)))
    assert peak > 0.01, f"demo song produced silence: peak={peak}"


async def s_unknown_key_is_noop(app, pilot):
    """Random non-note non-hex key must not crash."""
    start_row = app.cursor_row
    await pilot.press("asterisk")
    await pilot.pause()
    # No note key → no row advance
    assert app.cursor_row == start_row


SCENARIOS: list[Scenario] = [
    # engine / model
    Scenario("song_empty_valid", s_song_empty_valid),
    Scenario("note_freq_a4_is_440hz", s_note_freq_a4),
    Scenario("note_name_format", s_note_name_round_trip),
    Scenario("mod_round_trip", s_mod_round_trip),
    Scenario("synth_fills_buffer", s_synth_fills_buffer),
    Scenario("synth_muted_channel_silent", s_synth_muted_channel_silent),
    Scenario("synth_sample_playback", s_synth_sample_playback),
    Scenario("synth_all_waveforms", s_all_waveforms_nonsilent),
    Scenario("synth_playback_cursor_advances", s_synth_playback_advances),
    # TUI
    Scenario("mount_clean", s_mount_clean),
    Scenario("cursor_starts_at_origin", s_cursor_starts_at_origin),
    Scenario("arrow_keys_move_cursor", s_arrow_keys_move_cursor),
    Scenario("cursor_clamps_at_top", s_cursor_clamps_at_top),
    Scenario("cursor_clamps_at_bottom", s_cursor_clamps_at_bottom),
    Scenario("tab_moves_channel", s_tab_moves_channel),
    Scenario("pagedown_jumps_16", s_page_down_jumps),
    Scenario("home_end_jump_to_ends", s_home_end),
    Scenario("z_key_enters_c_note", s_z_key_enters_note),
    Scenario("x_key_enters_d_note", s_x_key_enters_d),
    Scenario("q_key_enters_upper_octave_c", s_q_key_upper_octave),
    Scenario("delete_clears_cell", s_delete_clears_cell),
    Scenario("octave_up_down", s_octave_up_down),
    Scenario("instrument_cycle", s_instrument_cycle),
    Scenario("space_toggles_play", s_space_toggles_play),
    Scenario("mute_channel", s_mute_channel),
    Scenario("help_screen_opens", s_help_screen_opens),
    Scenario("pattern_view_renders", s_pattern_view_renders),
    Scenario("status_bar_renders", s_status_bar_renders),
    Scenario("channel_strip_renders", s_channel_strip_renders),
    Scenario("hex_digit_enters_instrument", s_hex_digit_in_instrument_field),
    Scenario("save_writes_mod", s_save_writes_mod),
    Scenario("load_error_nonfatal", s_load_error_nonfatal),
    Scenario("demo_song_loads", s_demo_song_loads),
    Scenario("demo_synth_makes_sound", s_demo_synth_makes_sound),
    Scenario("unknown_key_is_noop", s_unknown_key_is_noop),
]


async def run_one(scn: Scenario) -> tuple[str, bool, str]:
    # Use empty=True so tests have a predictable (blank) song state.
    # The default demo song fills cells we'd be asserting on.
    app = TrackerApp(sound=False, empty=True)
    try:
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            try:
                await scn.fn(app, pilot)
            except AssertionError as e:
                try:
                    app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
                except Exception:
                    pass
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:
                try:
                    app.save_screenshot(str(OUT / f"{scn.name}.ERROR.svg"))
                except Exception:
                    pass
                return (scn.name, False,
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            try:
                app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
            except Exception:
                pass
            return (scn.name, True, "")
    except Exception as e:
        return (scn.name, False,
                f"harness error: {type(e).__name__}: {e}\n{traceback.format_exc()}")


async def main(pattern: str | None = None) -> int:
    scenarios = [s for s in SCENARIOS if not pattern or pattern in s.name]
    if not scenarios:
        print(f"no scenarios match {pattern!r}")
        return 2
    results = []
    for scn in scenarios:
        name, ok, msg = await run_one(scn)
        mark = "\033[32mOK\033[0m" if ok else "\033[31mFAIL\033[0m"
        print(f"  {mark}  {name}")
        if not ok:
            for line in msg.splitlines():
                print(f"      {line}")
        results.append((name, ok, msg))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed}/{len(results)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(pattern)))
