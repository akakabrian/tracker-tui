"""Demo song — something musical on first launch.

A 4-bar loop in C major: bassline on CH1 (square), chords on CH2 (triangle),
arpeggio on CH3 (saw), hats on CH4 (noise). Not a masterpiece — a 20-line
show-we-can-make-sound exhibit.
"""

from __future__ import annotations

from .song import Cell, Song


def demo_song() -> Song:
    s = Song.empty(channels=4, patterns=1)
    s.name = "tracker demo"
    s.bpm = 120
    s.speed = 6

    p = s.patterns[0]

    # Channel 0 — bass, half notes (C  G  A  F) — semitones C3=36, G3=43, A3=45, F3=41
    bass_pattern = [36, 43, 45, 41]  # 4 bars, one note per 16 rows
    for bar, note in enumerate(bass_pattern):
        row = bar * 16
        p.rows[row][0] = Cell(note=note, instrument=1, volume=56)
        # reinforce halfway
        p.rows[row + 8][0] = Cell(note=note, instrument=1, volume=40)

    # Channel 1 — chord pad (triangle, instrument 3), plays root every 8 rows
    chord_pattern = [48, 55, 57, 53]   # one octave above bass
    for bar, note in enumerate(chord_pattern):
        p.rows[bar * 16][1] = Cell(note=note, instrument=3, volume=48)
        p.rows[bar * 16 + 8][1] = Cell(note=note + 4, instrument=3, volume=40)  # major third

    # Channel 2 — arpeggio (saw, instrument 2) every 4 rows
    arp = [60, 64, 67, 72]             # C-E-G-C in octave 5
    for r in range(0, 64, 4):
        p.rows[r][2] = Cell(note=arp[(r // 4) % 4], instrument=2, volume=36)

    # Channel 3 — hats (noise, instrument 4), every 2 rows with accent
    for r in range(0, 64, 2):
        v = 48 if r % 8 == 0 else 24
        p.rows[r][3] = Cell(note=72, instrument=4, volume=v)

    s.order = [0]
    return s
