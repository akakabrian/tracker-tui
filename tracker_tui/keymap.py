"""Tracker-style note entry keymap.

Two octaves on the home + top rows, FT2 / Impulse Tracker convention.
  Lower row (Z-octave):  Z S X D C V G B H N J M
     white notes    Z X C V B N M
     black notes      S D   G H J
  Upper row (Q-octave): Q 2 W 3 E R 5 T 6 Y 7 U
     white notes    Q W E R T Y U
     black notes      2 3   5 6 7

The "base octave" is a UI setting (default 4 = C-4 = 130 Hz) so the
player can shift the whole keyboard up / down.

Returns a semitone offset relative to "C in the base octave". The
caller adds `base_octave * 12`.
"""

from __future__ import annotations

# offset relative to the low-C key
LOWER_ROW = {
    "z": 0,  "s": 1,  "x": 2,  "d": 3,  "c": 4,  "v": 5,
    "g": 6,  "b": 7,  "h": 8,  "n": 9,  "j": 10, "m": 11,
    "comma": 12, "l": 13, "period": 14, "semicolon": 15, "slash": 16,
}
UPPER_ROW = {
    "q": 12, "2": 13, "w": 14, "3": 15, "e": 16, "r": 17,
    "5": 18, "t": 19, "6": 20, "y": 21, "7": 22, "u": 23,
    "i": 24, "9": 25, "o": 26, "0": 27, "p": 28,
    "left_square_bracket": 29, "equals_sign": 30, "right_square_bracket": 31,
}


def note_for_key(key: str, base_octave: int) -> int | None:
    """Returns absolute semitone number, or None if key isn't a note.

    base_octave is clamped to 0..7 so we never return out-of-range
    semitones (our synth tolerates arbitrary int but it's easier to
    keep things inside the .mod's writable range).
    """
    # IMPORTANT: `or` would collapse `z` (offset 0) to None — use an
    # explicit "in" check instead.
    if key in LOWER_ROW:
        off = LOWER_ROW[key]
    elif key in UPPER_ROW:
        off = UPPER_ROW[key]
    else:
        return None
    base_octave = max(0, min(7, base_octave))
    return base_octave * 12 + off


# Hex-digit keyboard entry for instrument / volume / effect columns.
HEX_KEYS = {
    **{str(i): i for i in range(10)},
    "a": 10, "b": 11, "c": 12, "d": 13, "e": 14, "f": 15,
}


def hex_for_key(key: str) -> int | None:
    return HEX_KEYS.get(key)
