"""Textual TUI for the tracker.

Widgets:
  * PatternView  — the editor grid. Strip-based render_line for perf.
  * ChannelStrip — top meters row (peak bars + M/S flags).
  * StatusBar    — BPM, speed, pattern, cursor.
  * InstrumentPanel — right column, 31 slots.
  * ControlsPanel — key legend.
  * flash        — one-line transient messages.
"""

from __future__ import annotations

from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Size
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Footer, Header, RichLog, Static

from . import tiles
from .keymap import hex_for_key, note_for_key
from .mod_io import load_mod, save_mod
from .screens import HelpScreen
from .song import Cell, Song, note_name
from .synth import AudioEngine


# --------------------------------------------------------------------------
# Pattern editor widget
# --------------------------------------------------------------------------


class PatternView(Widget, can_focus=True):
    """Renders the current pattern. Cursor state lives on the App; we
    read it during render_line to draw the highlight."""

    def __init__(self, app_ref: "TrackerApp", **kw) -> None:
        super().__init__(**kw)
        self._app = app_ref

    def get_content_width(self, container: Size, viewport: Size) -> int:
        # Row gutter (3) + " " + NUM_CHANNELS * (cell + sep)
        chans = self._app.song.num_channels
        return 4 + chans * (tiles.CELL_WIDTH + 2) + 2

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        pat = self._app.current_pattern()
        return pat.num_rows if pat else 64

    def render_line(self, y: int) -> Strip:
        app = self._app
        pat = app.current_pattern()
        width = self.size.width
        if pat is None:
            return Strip.blank(width)

        # Compute visible row range by centering the cursor vertically.
        widget_h = self.size.height
        half = max(1, widget_h // 2)
        view_top = max(0, min(pat.num_rows - widget_h, app.cursor_row - half))
        row_idx = view_top + y
        if row_idx < 0 or row_idx >= pat.num_rows:
            return Strip.blank(width)

        segs: list[Segment] = []

        # Row gutter: "NNN "
        gutter_style = tiles.row_gutter_style(row_idx)
        segs.append(Segment(f"{row_idx:3d} ", gutter_style))

        # Background tint for beat rows
        row_bg: Style | None = None
        if row_idx == app.cursor_row:
            row_bg = tiles.S_CURSOR_ROW_BG
        elif row_idx == app.play_row_display:
            row_bg = tiles.S_PLAY_ROW_BG
        elif row_idx % 4 == 0:
            row_bg = tiles.S_BAR_ROW_BG

        for ch in range(pat.num_channels):
            cell = pat.cell(row_idx, ch)
            is_cursor_cell = (row_idx == app.cursor_row and ch == app.cursor_channel)
            for field in range(tiles.NUM_COLS):
                text, fg_style = _render_field(cell, field)
                # Possibly overlay cursor-field background
                if is_cursor_cell and app.cursor_field == field:
                    st = fg_style + tiles.S_CURSOR_FIELD_BG
                elif is_cursor_cell:
                    st = fg_style + tiles.S_CURSOR_CELL_BG
                else:
                    st = fg_style + row_bg if row_bg else fg_style
                segs.append(Segment(text, st))
                # field separator (space) — blend with row bg so the
                # highlight band looks continuous
                sep_style = row_bg or Style()
                if is_cursor_cell:
                    sep_style = tiles.S_CURSOR_CELL_BG
                segs.append(Segment(" ", sep_style))
            # channel separator
            segs.append(Segment(tiles.CHANNEL_SEP, tiles.S_SEP + (row_bg or Style())))

        # Pad to widget width.
        used = sum(len(s.text) for s in segs)
        if used < width:
            segs.append(Segment(" " * (width - used), row_bg or Style()))
        return Strip(segs)


def _render_field(cell: Cell, field: int) -> tuple[str, Style]:
    """Render one cell sub-field; returns (text, base style)."""
    if field == tiles.COL_NOTE:
        if cell.note is None:
            return ("---", tiles.S_EMPTY)
        return (note_name(cell.note), tiles.S_NOTE)
    if field == tiles.COL_INSTR:
        if cell.instrument is None:
            return ("..", tiles.S_EMPTY)
        return (f"{cell.instrument:02X}", tiles.S_INSTR)
    if field == tiles.COL_VOL:
        if cell.volume is None:
            return ("..", tiles.S_EMPTY)
        return (f"{cell.volume:02X}", tiles.S_VOL)
    if field == tiles.COL_EFFECT:
        if cell.effect is None and cell.param is None:
            return ("...", tiles.S_EMPTY)
        return (f"{(cell.effect or 0):X}{(cell.param or 0):02X}", tiles.S_EFFECT)
    return ("?", tiles.S_EMPTY)


# --------------------------------------------------------------------------
# Channel strip (meters)
# --------------------------------------------------------------------------


class ChannelStrip(Static):
    """Top row: per-channel header, mute/solo flag, 1-line peak meter."""

    def __init__(self, app_ref: "TrackerApp") -> None:
        super().__init__("", id="channel_strip")
        self._app = app_ref

    def refresh_panel(self) -> None:
        a = self._app
        meters = a.audio.meters()
        t = Text()
        # Headers line
        for ch in range(a.song.num_channels):
            header = f" CH{ch + 1} "
            style = tiles.S_HEADER
            if a.audio._solo == ch:
                style = tiles.S_SOLO
            elif a.audio.voices[ch].muted:
                style = tiles.S_MUTED
            if ch == a.cursor_channel:
                style = style + Style(reverse=True)
            t.append(header, style=style)
            t.append("  ")
        t.append("\n")

        # Flags line (M / S / -)
        for ch in range(a.song.num_channels):
            v = a.audio.voices[ch]
            if a.audio._solo == ch:
                flag, st = "S", tiles.S_SOLO
            elif v.muted:
                flag, st = "M", tiles.S_MUTED
            else:
                flag, st = "·", Style(color="rgb(120,120,140)")
            t.append(f"  {flag}  ", style=st)
            t.append("  ")
        t.append("\n")

        # Meter line — 5-cell bar per channel
        bar_cells = 5
        for ch in range(a.song.num_channels):
            level = min(1.0, meters[ch] if ch < len(meters) else 0.0)
            for i in range(bar_cells):
                # Light this segment if level exceeds threshold
                threshold = (i + 1) / bar_cells
                if level >= threshold:
                    g, s = tiles.meter_segment(min(1.0, level))
                    t.append(g, style=s)
                else:
                    t.append("▁", style=Style(color="rgb(50,50,62)"))
            t.append("  ")
        self.update(t)


# --------------------------------------------------------------------------
# Status bar
# --------------------------------------------------------------------------


class StatusBar(Static):
    def __init__(self, app_ref: "TrackerApp") -> None:
        super().__init__("", id="status_bar")
        self._app = app_ref

    def refresh_panel(self) -> None:
        a = self._app
        t = Text()
        t.append(" ♫ ", style="bold rgb(255,220,80)")
        t.append(f"{a.song.name}", style="bold rgb(220,220,240)")
        t.append("   bpm ", style="rgb(150,150,170)")
        t.append(f"{a.song.bpm}", style="bold rgb(220,220,240)")
        t.append("   spd ", style="rgb(150,150,170)")
        t.append(f"{a.song.speed}", style="bold rgb(220,220,240)")
        t.append("   ord ", style="rgb(150,150,170)")
        order_pos = a.current_order_idx
        t.append(f"{order_pos + 1}/{len(a.song.order)}",
                 style="bold rgb(220,220,240)")
        t.append("   pat ", style="rgb(150,150,170)")
        pat_idx = a.song.order[order_pos] if a.song.order else 0
        t.append(f"{pat_idx:02X}", style="bold rgb(220,220,240)")
        t.append("   row ", style="rgb(150,150,170)")
        t.append(f"{a.cursor_row:02d}", style="bold rgb(220,220,240)")
        t.append("   oct ", style="rgb(150,150,170)")
        t.append(f"{a.base_octave}", style="bold rgb(220,220,240)")
        t.append("   ins ", style="rgb(150,150,170)")
        ins_name = (a.song.instruments[a.current_instrument].name
                    if a.current_instrument < len(a.song.instruments)
                    else "")
        t.append(f"{a.current_instrument:02X} {ins_name}",
                 style="bold rgb(160,220,160)")
        if a.audio.playing:
            t.append("   ▶ PLAY", style="bold rgb(120,230,120)")
        self.update(t)


# --------------------------------------------------------------------------
# Side panel — instruments + controls
# --------------------------------------------------------------------------


class InstrumentPanel(Static):
    def __init__(self, app_ref: "TrackerApp") -> None:
        super().__init__("", id="instruments")
        self._app = app_ref

    def refresh_panel(self) -> None:
        a = self._app
        t = Text()
        t.append("Instruments\n", style="bold rgb(180,200,240)")
        # Show a window of 14 slots around the selected one
        total = min(31, len(a.song.instruments) - 1)
        window = 14
        sel = a.current_instrument
        start = max(1, min(total - window + 1, sel - window // 2))
        start = max(1, start)
        for i in range(start, min(total + 1, start + window)):
            ins = a.song.instruments[i]
            marker = "▸" if i == sel else " "
            style = "bold rgb(255,220,80)" if i == sel else "rgb(200,200,215)"
            t.append(f"{marker} {i:02X} ", style=style)
            kind = ins.waveform[:4] if ins.waveform else "----"
            t.append(f"{kind:<5}", style="rgb(160,220,160)")
            name = (ins.name or "")[:10]
            t.append(f"{name}\n", style=style)
        super().update(t)


class ControlsPanel(Static):
    def __init__(self) -> None:
        t = Text()
        t.append("Controls\n", style="bold rgb(180,200,240)")
        rows = [
            ("arrows",      "move cursor"),
            ("Tab",         "next channel"),
            ("Z X C V…",    "enter note"),
            ("- / =",       "octave -/+"),
            ("[ / ]",       "instrument -/+"),
            ("m / s",       "mute / solo"),
            ("Space",       "play pattern"),
            ("F5 / F8",     "play song / stop"),
            ("Del",         "clear cell"),
            ("Ctrl+S",      "save .mod"),
            ("?",           "help"),
            ("q",           "quit"),
        ]
        for k, desc in rows:
            t.append(f"  {k:<11}", style="bold rgb(255,220,80)")
            t.append(f"{desc}\n", style="rgb(200,200,215)")
        super().__init__(t, id="controls")


# --------------------------------------------------------------------------
# The App
# --------------------------------------------------------------------------


class TrackerApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "Tracker TUI"
    SUB_TITLE = ""

    BINDINGS = [
        # Navigation — priority so scrollable siblings don't steal arrows.
        Binding("up",        "cursor(-1, 0, 0)", show=False, priority=True),
        Binding("down",      "cursor( 1, 0, 0)", show=False, priority=True),
        Binding("left",      "cursor( 0, 0,-1)", show=False, priority=True),
        Binding("right",     "cursor( 0, 0, 1)", show=False, priority=True),
        Binding("pageup",    "cursor(-16, 0, 0)", show=False, priority=True),
        Binding("pagedown",  "cursor( 16, 0, 0)", show=False, priority=True),
        Binding("home",      "cursor_home", show=False, priority=True),
        Binding("end",       "cursor_end",  show=False, priority=True),
        Binding("tab",       "channel_next", show=False, priority=True),
        Binding("shift+tab", "channel_prev", show=False, priority=True),

        # Note-entry / hex-entry — handled in on_key (too many to bind).

        # Playback
        Binding("space",     "play_pause", "play"),
        Binding("f5",        "play_song",  "play song", show=False),
        Binding("f6",        "play_here",  "play from cursor", show=False),
        Binding("f8",        "stop",       "stop", show=False),

        # Edit
        Binding("delete",    "clear_cell", "clear", show=False, priority=True),
        Binding("backspace", "clear_cell", "clear", show=False, priority=True),
        Binding("minus",     "octave_down", show=False, priority=True),
        Binding("equals_sign","octave_up", show=False, priority=True),
        Binding("left_square_bracket",  "instrument(-1)", show=False, priority=True),
        Binding("right_square_bracket", "instrument( 1)", show=False, priority=True),

        # Pattern / order
        Binding("n",         "order_next", "next", show=False),
        Binding("N",         "order_prev", "prev", show=False),

        # Channel flags
        Binding("M",         "mute", show=False),
        Binding("S",         "solo", show=False),

        # File
        Binding("ctrl+s",    "save", "save"),

        # Meta
        Binding("question_mark", "help", "help"),
        Binding("f1",            "help", show=False),
        Binding("q",             "quit", "quit"),
        Binding("ctrl+q",        "quit", show=False),
    ]

    def __init__(self, module_path: str | None = None, sound: bool = True) -> None:
        super().__init__()
        if module_path:
            try:
                self.song = load_mod(module_path)
            except Exception as e:
                self.song = Song.empty()
                self._load_error = str(e)
            else:
                self._load_error = None
        else:
            self.song = Song.empty()
            self._load_error = None

        # Cursor state
        self.current_order_idx = 0
        self.cursor_row = 0
        self.cursor_channel = 0
        self.cursor_field = tiles.COL_NOTE  # 0..3
        self.base_octave = 4
        self.current_instrument = 1
        # What column within the cell counts as a "digit-entry" field?
        # The note field is a key-to-note mapping; instr/vol/effect cols
        # accept hex digits. We track which digit is next (high / low nib).
        self._hex_digit_pos = 0  # 0 = high nibble, 1 = low nibble

        # Playback display row (latest read of audio engine state)
        self.play_row_display: int | None = None

        self.audio = AudioEngine(self.song, sound=sound)

        # widgets — filled in compose()
        self.pattern_view: PatternView | None = None
        self.status_bar: StatusBar | None = None
        self.channel_strip: ChannelStrip | None = None
        self.instrument_panel: InstrumentPanel | None = None
        self.flash: Static | None = None
        self.message_log: RichLog | None = None
        self._save_path = Path("tracker.mod")

    # --- compose ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self.status_bar = StatusBar(self)
        self.channel_strip = ChannelStrip(self)
        self.pattern_view = PatternView(self, id="pattern")
        self.flash = Static("", id="flash")
        self.message_log = RichLog(id="log", max_lines=500, wrap=True, markup=True)
        # Prevent the log from stealing focus + tracker key bindings
        # (RichLog maps z/j/etc. to its own scroll controls).
        self.message_log.can_focus = False
        self.instrument_panel = InstrumentPanel(self)
        with Vertical(id="main"):
            yield self.status_bar
            yield self.channel_strip
            yield self.pattern_view
            yield self.flash
        with Vertical(id="side"):
            yield self.instrument_panel
            yield ControlsPanel()
            yield self.message_log
        yield Footer()

    def on_mount(self) -> None:
        self.audio.start()
        self._refresh_all()
        self.set_interval(0.1, self._tick_refresh)
        # Put focus on the pattern editor so tracker key bindings don't
        # compete with widget-local ones (RichLog grabs z/h/j/k/l etc).
        if self.pattern_view is not None:
            self.pattern_view.focus()
        if self._load_error:
            self._log(f"[bold rgb(240,120,120)]load error:[/] {self._load_error}")
        else:
            self._log(f"[bold rgb(180,200,240)]♫ tracker-tui[/] — {self.song.name}")

    def on_unmount(self) -> None:
        self.audio.stop()

    # --- accessors -------------------------------------------------------

    def current_pattern(self):
        if not self.song.order:
            return self.song.patterns[0] if self.song.patterns else None
        idx = self.song.order[self.current_order_idx]
        if 0 <= idx < len(self.song.patterns):
            return self.song.patterns[idx]
        return None

    # --- UI refresh ------------------------------------------------------

    def _refresh_all(self) -> None:
        if self.pattern_view:
            self.pattern_view.refresh()
        if self.channel_strip:
            self.channel_strip.refresh_panel()
        if self.status_bar:
            self.status_bar.refresh_panel()
        if self.instrument_panel:
            self.instrument_panel.refresh_panel()

    def _tick_refresh(self) -> None:
        # Keep the playback row and meters in sync.
        if self.audio.playing:
            self.play_row_display = self.audio.play_row
            if self.pattern_view:
                self.pattern_view.refresh()
        else:
            self.play_row_display = None
        if self.channel_strip:
            self.channel_strip.refresh_panel()
        if self.status_bar:
            self.status_bar.refresh_panel()

    def _log(self, msg: str) -> None:
        if self.message_log:
            self.message_log.write(msg)

    def _flash_msg(self, msg: str) -> None:
        if self.flash:
            self.flash.update(msg)

    # --- cursor actions --------------------------------------------------

    def action_cursor(self, drow: int, dchan: int, dfield: int) -> None:
        pat = self.current_pattern()
        if pat is None:
            return
        if dfield:
            self.cursor_field += dfield
            if self.cursor_field < 0:
                # wrap left into prev channel's effect col
                self.cursor_channel -= 1
                self.cursor_field = tiles.NUM_COLS - 1
            elif self.cursor_field >= tiles.NUM_COLS:
                self.cursor_channel += 1
                self.cursor_field = 0
        self.cursor_channel = max(0, min(pat.num_channels - 1, self.cursor_channel))
        self.cursor_row = max(0, min(pat.num_rows - 1, self.cursor_row + drow))
        self._hex_digit_pos = 0
        self._refresh_all()

    def action_cursor_home(self) -> None:
        self.cursor_row = 0
        self._refresh_all()

    def action_cursor_end(self) -> None:
        pat = self.current_pattern()
        if pat:
            self.cursor_row = pat.num_rows - 1
            self._refresh_all()

    def action_channel_next(self) -> None:
        pat = self.current_pattern()
        if pat:
            self.cursor_channel = (self.cursor_channel + 1) % pat.num_channels
            self.cursor_field = 0
            self._refresh_all()

    def action_channel_prev(self) -> None:
        pat = self.current_pattern()
        if pat:
            self.cursor_channel = (self.cursor_channel - 1) % pat.num_channels
            self.cursor_field = 0
            self._refresh_all()

    def action_octave_up(self) -> None:
        self.base_octave = min(7, self.base_octave + 1)
        self._flash_msg(f"octave = {self.base_octave}")
        if self.status_bar:
            self.status_bar.refresh_panel()

    def action_octave_down(self) -> None:
        self.base_octave = max(0, self.base_octave - 1)
        self._flash_msg(f"octave = {self.base_octave}")
        if self.status_bar:
            self.status_bar.refresh_panel()

    def action_instrument(self, delta: int) -> None:
        total = min(31, len(self.song.instruments) - 1)
        self.current_instrument = max(1, min(total, self.current_instrument + delta))
        self._refresh_all()

    # --- edit actions ----------------------------------------------------

    def action_clear_cell(self) -> None:
        pat = self.current_pattern()
        if pat is None:
            return
        pat.set_cell(self.cursor_row, self.cursor_channel, Cell())
        # Step row forward — classic tracker behaviour
        self.cursor_row = min(pat.num_rows - 1, self.cursor_row + 1)
        self._refresh_all()

    def _put_note(self, note: int) -> None:
        pat = self.current_pattern()
        if pat is None:
            return
        cell = pat.cell(self.cursor_row, self.cursor_channel)
        cell.note = note
        if cell.instrument is None:
            cell.instrument = self.current_instrument
        # preview play
        self.audio.trigger_preview(self.cursor_channel, note, cell.instrument or 1)
        # advance one row (configurable in real trackers; we hard-code 1)
        self.cursor_row = min(pat.num_rows - 1, self.cursor_row + 1)
        self._refresh_all()

    def _put_hex(self, val: int) -> None:
        """Enter a hex digit into instr / vol / effect field."""
        pat = self.current_pattern()
        if pat is None or self.cursor_field == tiles.COL_NOTE:
            return
        cell = pat.cell(self.cursor_row, self.cursor_channel)
        if self.cursor_field == tiles.COL_INSTR:
            cur = cell.instrument or 0
            if self._hex_digit_pos == 0:
                cell.instrument = (val << 4) | (cur & 0x0F)
            else:
                cell.instrument = (cur & 0xF0) | val
            cell.instrument = min(31, cell.instrument)
        elif self.cursor_field == tiles.COL_VOL:
            cur = cell.volume or 0
            if self._hex_digit_pos == 0:
                cell.volume = (val << 4) | (cur & 0x0F)
            else:
                cell.volume = (cur & 0xF0) | val
            cell.volume = min(64, cell.volume)
        elif self.cursor_field == tiles.COL_EFFECT:
            # Effect field is 3 nibbles: E PP. We cycle across all three.
            if self._hex_digit_pos == 0:
                cell.effect = val & 0x0F
            elif self._hex_digit_pos == 1:
                cur = cell.param or 0
                cell.param = (val << 4) | (cur & 0x0F)
            else:
                cur = cell.param or 0
                cell.param = (cur & 0xF0) | val
        self._hex_digit_pos += 1
        fields_nibbles = {tiles.COL_INSTR: 2, tiles.COL_VOL: 2, tiles.COL_EFFECT: 3}
        if self._hex_digit_pos >= fields_nibbles.get(self.cursor_field, 2):
            # advance to next row
            self._hex_digit_pos = 0
            self.cursor_row = min(pat.num_rows - 1, self.cursor_row + 1)
        self._refresh_all()

    # --- playback --------------------------------------------------------

    def action_play_pause(self) -> None:
        if self.audio.playing:
            self.audio.stop_play()
            self._flash_msg("stop")
        else:
            self.audio.play_from(self.current_order_idx, 0)
            self._flash_msg("play ▶")

    def action_play_song(self) -> None:
        self.audio.play_from(0, 0)
        self.current_order_idx = 0
        self._flash_msg("play song ▶")

    def action_play_here(self) -> None:
        self.audio.play_from(self.current_order_idx, self.cursor_row)
        self._flash_msg(f"play from row {self.cursor_row} ▶")

    def action_stop(self) -> None:
        self.audio.stop_play()
        self._flash_msg("stop")

    def action_order_next(self) -> None:
        if self.current_order_idx + 1 < len(self.song.order):
            self.current_order_idx += 1
            self.cursor_row = 0
            self._refresh_all()

    def action_order_prev(self) -> None:
        if self.current_order_idx > 0:
            self.current_order_idx -= 1
            self.cursor_row = 0
            self._refresh_all()

    def action_mute(self) -> None:
        v = self.audio.voices[self.cursor_channel]
        v.muted = not v.muted
        self._flash_msg(f"CH{self.cursor_channel + 1} {'muted' if v.muted else 'un-muted'}")
        if self.channel_strip:
            self.channel_strip.refresh_panel()

    def action_solo(self) -> None:
        self.audio.toggle_solo(self.cursor_channel)
        self._flash_msg(f"solo CH{self.cursor_channel + 1}")
        if self.channel_strip:
            self.channel_strip.refresh_panel()

    # --- save / load -----------------------------------------------------

    def action_save(self) -> None:
        try:
            save_mod(self.song, self._save_path)
            size = self._save_path.stat().st_size
            self._log(f"[rgb(160,220,160)]saved[/] {self._save_path} ({size} bytes)")
            self._flash_msg(f"saved {self._save_path}")
        except Exception as e:
            self._log(f"[bold rgb(240,120,120)]save failed:[/] {e}")
            self._flash_msg(f"save failed: {e}")

    # --- help ------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # --- key handler (note / hex entry) ----------------------------------

    def on_key(self, event) -> None:
        key = event.key
        # If the current cursor field is a hex field, try hex first.
        if self.cursor_field != tiles.COL_NOTE:
            v = hex_for_key(key)
            if v is not None:
                self._put_hex(v)
                event.stop()
                return
        # Otherwise: is it a note key?
        note = note_for_key(key, self.base_octave)
        if note is not None and self.cursor_field == tiles.COL_NOTE:
            self._put_note(note)
            event.stop()
            return


def run(module_path: str | None = None, sound: bool = True) -> None:
    TrackerApp(module_path=module_path, sound=sound).run()
