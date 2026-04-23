"""Core song data — patterns, cells, instruments.

The Song is the single source of truth. Everything else (synth, UI,
mod writer) reads / writes these structures. All fields are plain
Python (no numpy here) so serialising to JSON is trivial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# -- note helpers ----------------------------------------------------------

# We use absolute semitone numbers (0 = C-0) internally. Range used by
# trackers is roughly C-1 (12) to B-7 (95); .mod's period table covers
# C-1..B-3 natively but we store full MIDI-like range because our
# in-process synth supports it.
#
# Display form: "C-4" / "C#4" / "---" (empty). Lowercase for sharps is
# avoided (lookalike with 'b'). We stick to uppercase sharps.
NOTE_NAMES = ["C-", "C#", "D-", "D#", "E-", "F-",
              "F#", "G-", "G#", "A-", "A#", "B-"]


def note_name(n: Optional[int]) -> str:
    """Render a semitone number as 'C-4' / '---' style."""
    if n is None:
        return "---"
    octave = n // 12
    return f"{NOTE_NAMES[n % 12]}{octave}"


def note_freq(n: int) -> float:
    """MIDI-ish: A-4 = 440 Hz. Semitone n uses A4 = 69 as reference when
    mapped to midi (octave offset differs; tracker octaves are 1 lower
    than MIDI by convention — "A-4 in tracker" = 440 Hz). We match the
    Impulse Tracker / FastTracker-II convention: C-5 in tracker is
    middle-C (261.63 Hz)."""
    # tracker_octave: 0..8, with C-5 == MIDI 60 (middle C). Our `n` is
    # 12*octave + pc, so n == 60 → 261.63 Hz. A-5 is our n=69 and should
    # be 440 Hz (because we just numbered octaves starting at 0, so
    # "our A-5" == "MIDI A4" == 440). Equal-temperament from A4=440.
    return 440.0 * (2.0 ** ((n - 69) / 12.0))


# -- data classes ----------------------------------------------------------


@dataclass
class Cell:
    """One pattern cell (one row × one channel).

    All fields are Optional so an "empty" cell serializes as `---` / `..`
    / `.` / `...` the way classic trackers render it. We use None rather
    than 0 because 0 is a legal volume and instrument-0 means "reuse
    last instrument" in .mod.
    """
    note: Optional[int] = None        # semitone, 0..107
    instrument: Optional[int] = None  # 1..31 in .mod; we store 0-based None=empty
    volume: Optional[int] = None      # 0..64
    effect: Optional[int] = None      # 0..15 (.mod effect nibble)
    param: Optional[int] = None       # 0..255 (two hex nibbles)

    def is_empty(self) -> bool:
        return (self.note is None and self.instrument is None
                and self.volume is None and self.effect is None
                and self.param is None)


@dataclass
class Pattern:
    """A pattern is a grid of Cell: rows × channels."""
    num_rows: int = 64
    num_channels: int = 4
    rows: list[list[Cell]] = field(default_factory=list)

    @classmethod
    def empty(cls, rows: int = 64, channels: int = 4) -> "Pattern":
        p = cls(num_rows=rows, num_channels=channels)
        p.rows = [[Cell() for _ in range(channels)] for _ in range(rows)]
        return p

    def cell(self, row: int, ch: int) -> Cell:
        return self.rows[row][ch]

    def set_cell(self, row: int, ch: int, cell: Cell) -> None:
        self.rows[row][ch] = cell


@dataclass
class Instrument:
    """An instrument slot. Waveform OR sample drives the synth voice.

    waveform: one of "square" / "saw" / "triangle" / "sine" / "noise"
              / "sample".
    sample:   list[float] mono, -1..1, played at note's frequency (tuned
              so C-5 == 1.0× playback rate of the raw sample).
    """
    name: str = ""
    waveform: str = "square"
    sample: Optional[list[float]] = None
    volume: int = 64                  # 0..64
    loop_start: int = 0               # sample frames
    loop_end: int = 0                 # 0 means no loop
    # C-5 playback rate for samples, in Hz — classic .mod stores this
    # implicitly via finetune + the Amiga period table. We keep a flat
    # base so samples play at a sane pitch out of the box.
    base_rate: int = 8363


@dataclass
class Song:
    name: str = "untitled"
    patterns: list[Pattern] = field(default_factory=list)
    order: list[int] = field(default_factory=list)      # play order of pattern indices
    instruments: list[Instrument] = field(default_factory=list)
    bpm: int = 125
    speed: int = 6                                      # ticks per row (.mod default)
    num_channels: int = 4

    @classmethod
    def empty(cls, channels: int = 4, patterns: int = 4,
              instruments: int = 31) -> "Song":
        """Build a blank song with N empty patterns and 31 empty instrument slots.

        `.mod` restricts us to 31 instruments — we default to that so the
        UI has a stable slot count and saving doesn't silently truncate.
        """
        s = cls(num_channels=channels)
        s.patterns = [Pattern.empty(64, channels) for _ in range(patterns)]
        s.order = [0]  # one-pattern song by default
        # Slot 0 is reserved (classic trackers call instruments 1..31).
        # We keep it in the list but render "--" in the UI.
        s.instruments = [Instrument(name=f"ins{i:02d}") for i in range(instruments + 1)]
        s.instruments[0].name = ""
        # Seed a pleasant default in slot 1 so new users hear something.
        s.instruments[1].name = "square"
        s.instruments[1].waveform = "square"
        s.instruments[2].name = "saw"
        s.instruments[2].waveform = "saw"
        s.instruments[3].name = "triangle"
        s.instruments[3].waveform = "triangle"
        s.instruments[4].name = "noise"
        s.instruments[4].waveform = "noise"
        return s
