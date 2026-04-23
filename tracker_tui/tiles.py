"""Glyph + style tables for rendering pattern cells.

One cell row in the editor looks like:

    C-4 01 40 A05 │ --- .. .. ... │ ...

Columns within a cell: NOTE(3) INST(2) VOL(2) EFFECT(3) = 10 chars + 1
space = 11 chars per channel. We pre-compose Style objects so
render_line doesn't re-parse per cell.
"""

from __future__ import annotations

from rich.style import Style

# --- column widths inside a cell --------------------------------------

# Drawn as: "NNN II VV EPP" with single-space separators between fields.
#  NNN = note (3 chars, e.g. "C-4" or "---")
#  II  = instrument two-hex (or "..")
#  VV  = volume two-hex (or "..")
#  EPP = effect one-hex + 2-hex param (or "...")
CELL_WIDTH = 3 + 1 + 2 + 1 + 2 + 1 + 3   # = 13
CELL_GAP = " "                           # space between channels
CHANNEL_SEP = "│"                        # between channels on the row

COL_NOTE = 0
COL_INSTR = 1
COL_VOL = 2
COL_EFFECT = 3
NUM_COLS = 4


# --- styles -----------------------------------------------------------

# Row number gutter styles.
S_ROW_GUTTER = Style(color="rgb(110,110,130)")
S_ROW_GUTTER_BEAT = Style(color="rgb(200,180,100)", bold=True)  # every 4th row
S_ROW_GUTTER_BAR = Style(color="rgb(255,220,120)", bold=True)   # every 16th row

# Cell content styles.
S_EMPTY = Style(color="rgb(70,70,85)")         # dashes / dots
S_NOTE = Style(color="rgb(230,230,240)", bold=True)
S_INSTR = Style(color="rgb(160,220,160)")
S_VOL = Style(color="rgb(220,200,140)")
S_EFFECT = Style(color="rgb(200,160,230)")

# Cursor highlights.
S_CURSOR_ROW_BG = Style(bgcolor="rgb(30,34,48)")
S_CURSOR_CELL_BG = Style(bgcolor="rgb(60,64,96)")
S_CURSOR_FIELD_BG = Style(bgcolor="rgb(110,110,180)", color="rgb(255,255,255)", bold=True)

# Playback row highlight (distinct from edit cursor).
S_PLAY_ROW_BG = Style(bgcolor="rgb(40,20,40)")

# Row backgrounds — every 4th row gets a subtle tint for readability.
S_BAR_ROW_BG = Style(bgcolor="rgb(16,16,24)")

# Channel separator.
S_SEP = Style(color="rgb(60,60,78)")

# Channel header + strip.
S_HEADER = Style(color="rgb(180,200,240)", bold=True)
S_MUTED = Style(color="rgb(220,90,90)", bold=True)
S_SOLO = Style(color="rgb(255,220,80)", bold=True)

# Meter gradient — quiet → loud.
METER_GLYPHS = " ▁▂▃▄▅▆▇█"
S_METER = [
    Style(color="rgb(60,120,60)"),     # 1/8 quiet
    Style(color="rgb(80,150,80)"),
    Style(color="rgb(110,180,100)"),
    Style(color="rgb(160,200,110)"),
    Style(color="rgb(210,200,100)"),
    Style(color="rgb(230,180,80)"),
    Style(color="rgb(240,130,70)"),
    Style(color="rgb(240,80,60)"),     # peak loud
]


def row_gutter_style(row: int) -> Style:
    if row % 16 == 0:
        return S_ROW_GUTTER_BAR
    if row % 4 == 0:
        return S_ROW_GUTTER_BEAT
    return S_ROW_GUTTER


def meter_segment(level: float) -> tuple[str, Style]:
    """Map 0..1 → (glyph, style) for a 1-char meter tick."""
    if level <= 0:
        return (" ", S_EMPTY)
    idx = min(len(METER_GLYPHS) - 1, max(1, int(level * (len(METER_GLYPHS) - 1))))
    sidx = min(len(S_METER) - 1, idx - 1)
    return (METER_GLYPHS[idx], S_METER[sidx])
