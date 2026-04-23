# tracker-tui

A terminal music tracker — step-sequencer for chiptune / module music.
Textual-based pattern editor driving an in-process software synth through
sounddevice.

Think Impulse Tracker / FastTracker II / Renoise, but as a TUI.

## Status

- 4 channels × 64 rows pattern editor with cursor navigation
- ZXCV / QWERTY note entry (two octaves, `-`/`=` to shift base octave)
- 5-waveform synth: square / saw / triangle / sine / noise + sample playback
- Channel strip with peak meters and mute/solo
- Save / load ProTracker 4-channel `.mod` files
- Built-in demo song ready on first launch
- 35 QA scenarios, all green

## Quick start

```bash
make all       # create venv
make run       # launch the tracker (loads demo song, press SPACE to play)
```

Or with a specific module file:

```bash
.venv/bin/python tracker.py path/to/some.mod
```

## Keys

| Key                           | Action                                   |
|-------------------------------|------------------------------------------|
| arrows                        | Move cursor                              |
| PageUp / PageDown             | Jump 16 rows                             |
| Home / End                    | Top / bottom of pattern                  |
| Tab / Shift+Tab               | Next / prev channel                      |
| Z X C V B N M , . / ;         | Note entry (lower octave)                |
| Q W E R T Y U I O P etc.      | Note entry (upper octave)                |
| `-` / `=`                     | Base octave down / up                    |
| `[` / `]`                     | Select instrument -/+                    |
| 0-9, a-f                      | Hex digit in instrument/volume/effect    |
| Space                         | Play / stop current pattern              |
| F5                            | Play song from start                     |
| F6                            | Play from cursor row                     |
| F8                            | Stop                                     |
| Delete / Backspace            | Clear cell                               |
| Shift+M                       | Mute current channel                     |
| Shift+S                       | Solo current channel                     |
| Ctrl+S                        | Save (tracker.mod)                       |
| F1 / `?`                      | Help                                     |
| q                             | Quit                                     |

## Development

```bash
make test          # full QA harness
make test-only PAT=synth   # subset by name
make perf          # hot-path benchmarks
make render-mod    # write a demo.mod via the pure-Python writer
```

See `DECISIONS.md` for the engine / format decisions and project layout.
