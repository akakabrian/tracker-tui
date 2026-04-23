# Tracker TUI — Design Decisions

A terminal music tracker (Renoise / Impulse Tracker / FastTracker II lineage)
built as a Textual app with an in-process software synth driving
`sounddevice`.

## Stage 1 — Research

**Canonical references consulted:**

- **OpenMPT / libopenmpt** — gold-standard Win32 tracker + portable playback
  library. `openmpt123` CLI is installed on this box. *libopenmpt reads*
  `.mod/.xm/.it/.s3m/...` but is read-only (playback); authoring + save is
  left to the host.
- **.mod (ProTracker) file format** — the spec that defines the whole
  category. 4 channels, 31 (or 15) sample slots, 128-entry pattern order
  table, 64-row patterns, 4 bytes per cell (period + sample-hi / sample-lo
  + effect + param). Small, fully documented, open.
- **ZXCV keyboard layout for note entry** — FastTracker II convention:
  two octaves across `ZXCVBNM,./` (lower) and `QWERTYUIOP[]` (upper),
  black keys on the row above. Every modern tracker uses this; no
  original-reference needed to copy it.
- **Renoise** — reference for the modern TUI layout (pattern editor
  centered, channel strips with meters, instrument list, sample browser).

**Engine / format decisions:**

- **Playback: pure-Python software synth driving `sounddevice`.** Faster
  path to "sound comes out" than wrangling SWIG / ctypes against
  `libopenmpt` (for which the distro ships no dev headers here and whose
  Python binding `py-openmpt` is essentially unmaintained). The synth
  generates mono samples on-the-fly: per-channel square / triangle / saw /
  noise oscillators with ADSR envelopes, plus a sample playback path for
  WAV blobs loaded into instrument slots. Mix to stereo float32 at 44100
  Hz, stream into `sounddevice.OutputStream` via an async queue. If no
  audio device is present, the stream is a no-op and the UI still works.
- **Optional fallback: render → wav → `openmpt123` / `aplay`.** For "play
  a loaded .mod file" we shell out to `openmpt123` so the user can audit
  a real module. Not used for authoring.
- **Authoring format: internal tracker JSON → writes canonical 4-channel
  `.mod`.** The JSON is the source of truth (save/load); the `.mod`
  writer is a pure-Python byte-emitter so users can export and load the
  result in any tracker / player. We target ProTracker-compat `.mod` only
  for v1 (`.xm` / `.it` read through `openmpt123` preview for now).
- **Read path:** a lightweight ProTracker `.mod` parser (our own — the
  format is small) loads notes + effects into the internal song
  structure. Sample *data* from the module is loaded into the synth as
  8-bit signed PCM, which the sample engine can play directly.

## Stage 2 — Engine

`tracker_tui/song.py` — pure Python dataclasses:

```python
@dataclass
class Cell:
    note: int | None          # MIDI-like semitone, 0..107 (C-0..B-8), None=empty
    instrument: int | None    # 1..31 or None
    volume: int | None        # 0..64, None=unset
    effect: int | None        # 0..15 (.mod effect nibble), None=none
    param: int | None         # 0..255

@dataclass
class Pattern:
    rows: list[list[Cell]]    # rows[row][channel]
    num_rows: int = 64
    num_channels: int = 4

@dataclass
class Instrument:
    name: str
    waveform: str             # "square" / "saw" / "triangle" / "noise" / "sample"
    sample: np.ndarray | None # mono float32 if waveform=="sample"
    volume: int = 64
    loop_start: int = 0
    loop_end: int = 0

@dataclass
class Song:
    patterns: list[Pattern]
    order: list[int]          # play-order of pattern indices
    instruments: list[Instrument]
    bpm: int = 125
    speed: int = 6            # ticks per row (classic .mod default)
    name: str = "untitled"
```

`tracker_tui/synth.py` — audio mixer. One `Voice` per channel; voices
are stepped each *tick* (`60/bpm/24 * speed` seconds per row), notes
trigger envelope retrigger, sample loop handled per-voice. The mixer
callback fills a `numpy` buffer in float32 and hands it to sounddevice.

`tracker_tui/mod_io.py` — ProTracker `.mod` reader + writer.

**Gate:** `Song.empty(4, 32)` returns a zeroed 32-pattern song;
`synth.play(song, from_order=0)` begins streaming; pressing a note key
while editing inserts the right Cell.

## Stage 3 — TUI scaffold

Panels (mirrors simcity-tui / sokoban-tui):

- **Pattern editor** (center, fills most width). Cursor = (row, channel,
  column-in-cell). Scrolls vertically; channels fit horizontally for
  4–8 channels.
- **Status bar** (top) — song name, BPM, speed, current pattern / order
  position, play-state.
- **Channel strip row** (above pattern) — 4 bars showing per-channel peak
  meter, `M` mute, `S` solo flags.
- **Instrument list** (right column top) — 31 slots, selected slot
  highlighted.
- **Help / controls panel** (right column bottom) — key legend.
- **Flash bar + log** (footer).

## Stage 4 — QA harness

Scenarios cover:

1. Song.empty() gives a valid structure.
2. Note entry via ZXCV inserts correct semitone.
3. Cursor arrow-key navigation (row/channel/column).
4. Cursor clamps at grid bounds.
5. Delete clears a cell.
6. `.mod` round-trip (write → read → equal).
7. Synth can render N samples without crashing (no device).
8. Play/stop changes `app.playing`.
9. Mute/solo affect the mix (silence on muted channel).
10. Board render produces styled segments.

## Stage 5+ (later)

- Perf: the hot path is synth mixing (must not starve the audio
  callback) and pattern-editor render (re-render only dirty rows).
- Robustness: no-device path, malformed .mod, unknown note glyph.
- Polish phases A-G — UI beauty, sample browser, save/load dialogs,
  agent REST API (record/playback via JSON), extra effects, etc.

## Controls (target)

| Key                           | Action                                   |
|-------------------------------|------------------------------------------|
| `↑` / `↓`                     | Move cursor row                          |
| `←` / `→`                     | Move cursor column (within / across cells)|
| `PageUp` / `PageDown`         | Jump 16 rows                             |
| `Home` / `End`                | Row 0 / last row                         |
| `Tab` / `Shift+Tab`           | Next / prev channel                      |
| `Z X C V B N M , . /` etc.    | Note entry (two-octave ZXCV layout)      |
| `1`–`9`, `0`, hex `a`–`f`     | Type digits in instr/vol/effect columns  |
| `Space`                       | Play / stop pattern                      |
| `F5`                          | Play song from start                     |
| `F6`                          | Play from cursor row                     |
| `F8`                          | Stop                                     |
| `Delete` / `Backspace`        | Clear cell                               |
| `Ctrl+S`                      | Save (write .mod / JSON)                 |
| `Ctrl+O`                      | Open (load .mod)                         |
| `F1` / `?`                    | Help modal                               |
| `q` / `Ctrl+Q`                | Quit                                     |
