"""Software synth + mix engine.

Per-channel `Voice` with oscillator or sample playback, simple ADSR,
and global volume. Mixer fills a float32 buffer of shape (frames, 2)
which we hand to sounddevice.

Tracker timing:
  - `speed` = ticks per row (default 6)
  - `tempo` / `bpm` controls tick length: one tick = 2.5 / bpm seconds.
    So at bpm=125, tick = 20 ms and row = 120 ms (classic ProTracker).

We don't try to faithfully reproduce every ProTracker effect — the
synth is here so the editor feels alive. Full .mod playback goes
through `openmpt123` if you want 1:1 authenticity.

Audio device is OPTIONAL. If sounddevice isn't installed or fails to
open a stream, `AudioEngine.start()` becomes a logged no-op and the
rest of the UI keeps working.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .song import Cell, Instrument, Song, note_freq

SAMPLE_RATE = 44100
CHANNELS_OUT = 2  # always stereo output; we mix mono voices and pan them


# ---------------------------------------------------------------------------
# Voice — one active note on one channel
# ---------------------------------------------------------------------------


@dataclass
class Voice:
    """Single polyphonic voice. Steps per-sample via `fill(buf)`."""
    active: bool = False
    freq: float = 440.0
    phase: float = 0.0
    waveform: str = "square"
    volume: float = 0.5                  # 0..1 master gain for this note
    # envelope (very simple — attack ramp + release)
    env: float = 0.0
    env_target: float = 1.0
    attack_rate: float = 0.0             # per-sample increment while attacking
    release_rate: float = 1.0 / (SAMPLE_RATE * 0.15)  # release over ~150 ms
    releasing: bool = False
    # sample playback
    sample: Optional[np.ndarray] = None
    sample_pos: float = 0.0
    sample_step: float = 1.0             # frames per output frame
    loop_start: int = 0
    loop_end: int = 0                    # 0 = no loop
    # per-channel
    muted: bool = False
    # peak meter (sampled by UI; reset each mix call)
    peak: float = 0.0
    # stereo panning per classic .mod: ch0,3 left; ch1,2 right.
    pan_l: float = 1.0
    pan_r: float = 1.0

    def trigger(self, freq: float, waveform: str, volume: float,
                sample: Optional[np.ndarray] = None,
                base_rate: int = 8363,
                loop_start: int = 0, loop_end: int = 0) -> None:
        """(Re)start this voice on a new note."""
        self.active = True
        self.freq = freq
        self.waveform = waveform
        self.volume = max(0.0, min(1.0, volume))
        self.phase = 0.0
        self.env = 0.0
        self.env_target = 1.0
        # ~5 ms attack — enough to avoid clicks
        self.attack_rate = 1.0 / (SAMPLE_RATE * 0.005)
        self.releasing = False
        self.sample = sample
        self.sample_pos = 0.0
        # sample playback rate: the sample's "native C-5" is `base_rate`
        # frames per second; at note frequency F we play at F/C5_freq ratio.
        c5 = note_freq(60)
        self.sample_step = (freq / c5) * (base_rate / SAMPLE_RATE)
        self.loop_start = loop_start
        self.loop_end = loop_end

    def note_off(self) -> None:
        self.releasing = True
        self.env_target = 0.0

    def fill(self, out_l: np.ndarray, out_r: np.ndarray) -> None:
        """Additively mix this voice into the two output buffers."""
        if not self.active or self.muted:
            return
        n = out_l.shape[0]

        # Envelope per-sample is expensive in pure Python — approximate with
        # a linear ramp over the block. For a 20 ms block (~882 samples)
        # at 44.1 kHz this is imperceptible.
        env_start = self.env
        if self.releasing:
            self.env = max(0.0, self.env - self.release_rate * n)
            if self.env == 0.0:
                self.active = False
        else:
            # attack toward 1.0
            self.env = min(1.0, self.env + self.attack_rate * n)
        env_end = self.env
        env = np.linspace(env_start, env_end, n, dtype=np.float32)

        if self.waveform == "sample" and self.sample is not None:
            block = self._fill_sample(n)
        else:
            block = self._fill_osc(n)

        gain = self.volume * env
        block *= gain

        self.peak = max(self.peak, float(np.max(np.abs(block))) if n else 0.0)

        out_l += block * self.pan_l
        out_r += block * self.pan_r

    # --- internal oscillators -----------------------------------------

    def _fill_osc(self, n: int) -> np.ndarray:
        """Generate one block of waveform. Returns float32 (n,)."""
        step = self.freq / SAMPLE_RATE
        # Phase at end-of-block.
        t = self.phase + np.arange(n, dtype=np.float32) * step
        self.phase = (self.phase + n * step) % 1.0

        w = self.waveform
        if w == "square":
            block = np.where((t % 1.0) < 0.5, 0.4, -0.4).astype(np.float32)
        elif w == "saw":
            block = (2.0 * (t % 1.0) - 1.0).astype(np.float32) * 0.4
        elif w == "triangle":
            frac = (t % 1.0).astype(np.float32)
            block = (np.abs(frac - 0.5) * 4.0 - 1.0) * 0.4
        elif w == "sine":
            block = np.sin(t * 2.0 * math.pi).astype(np.float32) * 0.4
        elif w == "noise":
            block = (np.random.random(n).astype(np.float32) * 2.0 - 1.0) * 0.4
        else:
            block = np.zeros(n, dtype=np.float32)
        return block

    def _fill_sample(self, n: int) -> np.ndarray:
        """Interpolated sample playback with optional loop."""
        s = self.sample
        assert s is not None
        block = np.zeros(n, dtype=np.float32)
        pos = self.sample_pos
        step = self.sample_step
        length = len(s)
        loop_len = self.loop_end - self.loop_start if self.loop_end > self.loop_start else 0
        for i in range(n):
            ip = int(pos)
            if ip >= length:
                if loop_len > 0:
                    ip = self.loop_start + ((ip - self.loop_start) % loop_len)
                    pos = float(ip)
                else:
                    # end of one-shot
                    self.active = False
                    break
            # linear interp
            frac = pos - ip
            a = s[ip]
            b = s[ip + 1] if ip + 1 < length else a
            block[i] = a * (1.0 - frac) + b * frac
            pos += step
        self.sample_pos = pos
        return block


# ---------------------------------------------------------------------------
# AudioEngine — mixer + playback cursor
# ---------------------------------------------------------------------------


class AudioEngine:
    """Owns the playback cursor and an output stream. Threadsafe
    trigger_preview() for "play this note right now" without advancing
    the song position.
    """

    def __init__(self, song: Song, sound: bool = True) -> None:
        self.song = song
        self.voices: list[Voice] = [Voice() for _ in range(max(song.num_channels, 4))]
        # default stereo panning — classic ProTracker LRRL on 4 channels.
        pan_table = [(1.0, 0.3), (0.3, 1.0), (0.3, 1.0), (1.0, 0.3)]
        for i, v in enumerate(self.voices):
            l, r = pan_table[i % 4]
            v.pan_l = l
            v.pan_r = r

        self._solo: int | None = None   # channel index or None
        self._lock = threading.Lock()

        # playback cursor
        self.playing = False
        self.play_order = 0             # index into song.order
        self.play_row = 0
        self.play_tick = 0
        self._samples_to_next_tick = 0

        self._sd = None
        self._stream = None
        if sound:
            try:
                import sounddevice as sd  # type: ignore
                self._sd = sd
            except Exception:  # ImportError, or OSError from libportaudio
                self._sd = None

    # --- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Open the output stream. If no audio is available, no-op."""
        if self._sd is None or self._stream is not None:
            return
        try:
            self._stream = self._sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS_OUT,
                dtype="float32",
                blocksize=0,   # let PA choose
                callback=self._callback,
            )
            self._stream.start()
        except Exception:
            # No device, no permission, backend dead — silent fallback.
            self._stream = None

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # --- playback cursor ---------------------------------------------

    def play_from(self, order_idx: int = 0, row: int = 0) -> None:
        with self._lock:
            if not self.song.order:
                return
            self.play_order = max(0, min(order_idx, len(self.song.order) - 1))
            self.play_row = max(0, min(row, 63))
            self.play_tick = 0
            self._samples_to_next_tick = 0
            self.playing = True

    def stop_play(self) -> None:
        with self._lock:
            self.playing = False
            for v in self.voices:
                v.note_off()

    # --- interactive note preview ------------------------------------

    def trigger_preview(self, channel: int, note: int, instrument: int) -> None:
        """Play a note on the given channel without affecting song
        playback. Used for keyboard-entry preview."""
        with self._lock:
            self._trigger_channel(channel, note, instrument, volume=64)

    def _trigger_channel(self, ch: int, note: int, instrument: int,
                         volume: int = 64) -> None:
        if ch < 0 or ch >= len(self.voices):
            return
        ins = (self.song.instruments[instrument]
               if 0 <= instrument < len(self.song.instruments)
               else None)
        if ins is None or not ins.waveform:
            # fall back to square so dev always hears something
            waveform = "square"
            sample = None
            base_rate = 8363
            loop_start = loop_end = 0
        else:
            waveform = ins.waveform
            base_rate = ins.base_rate
            loop_start = ins.loop_start
            loop_end = ins.loop_end
            if waveform == "sample" and ins.sample is not None:
                sample = np.asarray(ins.sample, dtype=np.float32)
            else:
                sample = None
        vol = (ins.volume if ins else 64) * volume / 64.0 / 64.0
        self.voices[ch].trigger(
            freq=note_freq(note),
            waveform=waveform,
            volume=float(vol),
            sample=sample,
            base_rate=base_rate,
            loop_start=loop_start,
            loop_end=loop_end,
        )

    # --- meter / mute / solo ------------------------------------------

    def mute(self, ch: int, flag: bool) -> None:
        if 0 <= ch < len(self.voices):
            self.voices[ch].muted = flag

    def toggle_solo(self, ch: int) -> None:
        if self._solo == ch:
            self._solo = None
            for v in self.voices:
                v.muted = False
        else:
            self._solo = ch
            for i, v in enumerate(self.voices):
                v.muted = (i != ch)

    def meters(self) -> list[float]:
        """Return + reset peak meters (one float per channel, 0..1)."""
        m = [v.peak for v in self.voices]
        for v in self.voices:
            v.peak = 0.0
        return m

    # --- the audio callback ------------------------------------------

    def _callback(self, outdata, frames, time_info, status) -> None:
        # Allocate once — outdata is writeable float32 (frames, 2).
        outdata.fill(0.0)
        left = np.zeros(frames, dtype=np.float32)
        right = np.zeros(frames, dtype=np.float32)

        # Advance the song cursor in a loop that may cross tick
        # boundaries mid-block.
        remaining = frames
        offset = 0
        with self._lock:
            while remaining > 0:
                if self.playing and self._samples_to_next_tick == 0:
                    self._on_tick()
                step = min(remaining, self._samples_to_next_tick or remaining)
                if step > 0:
                    self._fill_block(
                        left[offset:offset + step],
                        right[offset:offset + step],
                    )
                if self.playing:
                    self._samples_to_next_tick -= step
                remaining -= step
                offset += step

        # soft-clip to ±1 to protect ears.
        np.clip(left, -1.0, 1.0, out=left)
        np.clip(right, -1.0, 1.0, out=right)
        outdata[:, 0] = left
        outdata[:, 1] = right

    def _fill_block(self, out_l: np.ndarray, out_r: np.ndarray) -> None:
        for v in self.voices:
            v.fill(out_l, out_r)

    def _on_tick(self) -> None:
        """Fire any note triggers for the current (order, row, tick)."""
        song = self.song
        if not song.order:
            self.playing = False
            return
        pat_idx = song.order[self.play_order]
        if pat_idx >= len(song.patterns):
            self.playing = False
            return
        pattern = song.patterns[pat_idx]
        # On tick 0 of a row, fire note triggers.
        if self.play_tick == 0:
            for ch in range(pattern.num_channels):
                if ch >= len(self.voices):
                    continue
                cell: Cell = pattern.cell(self.play_row, ch)
                if cell.note is not None:
                    instr = cell.instrument or 1
                    vol = cell.volume if cell.volume is not None else 64
                    self._trigger_channel(ch, cell.note, instr, vol)
        # Schedule next tick.
        tick_len = max(1, int(SAMPLE_RATE * 2.5 / max(song.bpm, 32)))
        self._samples_to_next_tick = tick_len
        self.play_tick += 1
        if self.play_tick >= song.speed:
            self.play_tick = 0
            self.play_row += 1
            if self.play_row >= pattern.num_rows:
                self.play_row = 0
                self.play_order += 1
                if self.play_order >= len(song.order):
                    self.play_order = 0  # loop
