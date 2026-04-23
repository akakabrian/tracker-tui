"""Modal screens for tracker-tui."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,f1", "dismiss", "close")]

    def compose(self):
        body = Text()
        body.append("Tracker TUI — help\n", style="bold rgb(180,200,240)")
        body.append("\n")
        rows = [
            ("arrows",              "move pattern cursor"),
            ("PageUp / PageDown",   "jump 16 rows"),
            ("Home / End",          "top / bottom of pattern"),
            ("Tab / Shift+Tab",     "next / prev channel"),
            ("Z S X D C V …",       "note entry (lower octave)"),
            ("Q 2 W 3 E R …",       "note entry (upper octave)"),
            ("- / =",               "shift base octave down / up"),
            ("0-9, a-f",            "hex digit in instr/vol/effect cols"),
            ("Delete / Backspace",  "clear cell"),
            ("Space",               "play / stop pattern"),
            ("F5",                  "play song from start"),
            ("F6",                  "play from cursor row"),
            ("F8",                  "stop"),
            ("n / N",               "next / prev pattern in order"),
            ("[ / ]",                "select instrument -/+"),
            ("m / s",                "mute / solo current channel"),
            ("ctrl+s",              "save .mod (to tracker.mod)"),
            ("F1 / ?",              "this help"),
            ("q",                   "quit"),
        ]
        for k, desc in rows:
            body.append(f"  {k:<22}", style="bold rgb(255,220,80)")
            body.append(f"{desc}\n", style="rgb(200,200,215)")
        body.append("\n press Esc to close", style="rgb(150,150,170)")
        yield Vertical(Static(body, id="help-panel"))

    def action_dismiss(self) -> None:
        self.app.pop_screen()
