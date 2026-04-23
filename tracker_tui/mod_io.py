"""ProTracker .mod read + write.

Format reference: ProTracker 2/3 + FT2 behaviour (the `M.K.` magic
signals 4-channel standard .mod). Spec summary:

  0x000..0x014  20 bytes  song title (ASCII, NUL-padded)
  0x014..0x3B8  31×30 byte instrument headers:
      0x00..0x15  22 bytes  sample name
      0x16..0x17  u16 BE    sample length (in *words*, so *2 for bytes)
      0x18        u8        finetune (lower nibble, signed -8..7)
      0x19        u8        volume 0..64
      0x1A..0x1B  u16 BE    loop start (words)
      0x1C..0x1D  u16 BE    loop length (words)   (<=2 → no loop)
  0x3B8          u8         song length (# orders used, 1..128)
  0x3B9          u8         restart byte (historical; we write 127)
  0x3BA..0x43A   128 bytes  order table (pattern indices)
  0x438..0x43C   4 bytes    magic, "M.K." or similar (4-channel)
  [patterns]     N × (64 * 4 * 4) bytes, packed big-endian cells
  [samples]      sample data, each instrument's length in bytes (8-bit signed PCM)

Cell packing (4 bytes per cell, big endian-ish across bytes):
  byte0 = (instrument_hi << 4) | period_hi_nibble
  byte1 = period_low_byte
  byte2 = (instrument_lo << 4) | effect
  byte3 = effect_param

We use a small period table for C-1..B-3 (36 notes) — ProTracker's
historical Amiga range. Notes outside that range round to nearest.
Our synth uses the full chromatic range regardless, so writing out
a note slightly out of .mod range just quantises on export.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

from .song import Cell, Instrument, Pattern, Song


# ProTracker period table, one entry per semitone C-1..B-3 (36 entries).
# A period of N corresponds to the Amiga paula clock divided by N. We
# only need approximate mapping for round-trip authoring; playback is
# handled by our synth using real frequencies.
PERIOD_TABLE = [
    856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453,  # C-1..B-1
    428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226,  # C-2..B-2
    214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113,  # C-3..B-3
]

# Offset inside our absolute semitone space: .mod "C-1" is our semitone (12*1+0) = 12?
# In our scheme, semitone 60 == C-5 (middle C). .mod's C-1 aligns to ProTracker's
# lowest note which historically is labelled "C-1". To keep round-trip stable
# we treat .mod's C-1 as our semitone 36 (one octave above our C-2 "C-3 tracker"
# convention). This is a cosmetic mapping — users see the same note name back.
MOD_C1_SEMITONE = 36


def _note_to_period(note: int) -> int:
    idx = note - MOD_C1_SEMITONE
    if idx < 0:
        idx = 0
    elif idx >= len(PERIOD_TABLE):
        idx = len(PERIOD_TABLE) - 1
    return PERIOD_TABLE[idx]


def _period_to_note(period: int) -> Optional[int]:
    if period == 0:
        return None
    # nearest match
    best = 0
    best_d = abs(PERIOD_TABLE[0] - period)
    for i, p in enumerate(PERIOD_TABLE):
        d = abs(p - period)
        if d < best_d:
            best, best_d = i, d
    return MOD_C1_SEMITONE + best


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_mod(song: Song, path: str | Path) -> None:
    """Write `song` to a 4-channel ProTracker .mod file.

    Only 4 channels + 31 instruments are written (the rest are
    ignored). Instruments without sample data get a zero-length entry.
    """
    path = Path(path)
    out = bytearray()

    # 20-byte title
    title = song.name.encode("ascii", errors="replace")[:20]
    out += title + b"\x00" * (20 - len(title))

    # 31 instrument headers
    # We always write 31 slots; slot 0 in our Song is reserved and not emitted.
    ins_data: list[bytes] = []
    for i in range(1, 32):
        ins = (song.instruments[i] if i < len(song.instruments)
               else Instrument())
        name = ins.name.encode("ascii", errors="replace")[:22]
        name += b"\x00" * (22 - len(name))
        # Only real samples get data. Oscillator waveforms get an empty slot.
        sample_bytes = b""
        if ins.waveform == "sample" and ins.sample:
            # Quantise -1..1 float to signed 8-bit.
            sample_bytes = bytes(
                max(-128, min(127, int(x * 127.0))) & 0xFF
                for x in ins.sample
            )
        # .mod stores length in *words*. Round up to even byte count.
        if len(sample_bytes) % 2:
            sample_bytes += b"\x00"
        sample_words = len(sample_bytes) // 2
        finetune = 0
        vol = max(0, min(64, ins.volume))
        loop_start_words = ins.loop_start // 2
        loop_len_words = max(1, (ins.loop_end - ins.loop_start) // 2) if ins.loop_end > ins.loop_start else 1
        hdr = name
        hdr += struct.pack(">H", sample_words & 0xFFFF)
        hdr += struct.pack(">BB", finetune & 0x0F, vol)
        hdr += struct.pack(">HH", loop_start_words & 0xFFFF, loop_len_words & 0xFFFF)
        out += hdr
        ins_data.append(sample_bytes)

    # Song length + restart + order table (128 bytes, zero-padded)
    song_len = max(1, min(128, len(song.order)))
    out += bytes([song_len, 127])
    order = list(song.order[:128])
    order += [0] * (128 - len(order))
    out += bytes(order)

    # Magic — 4 channels = "M.K."
    out += b"M.K."

    # Patterns (only those referenced in the order table, plus any higher-
    # numbered patterns up to `max(order)`). Typical trackers write up to
    # `max(order) + 1` patterns.
    num_patterns = max(order) + 1 if any(order) or song.order else 1
    num_patterns = min(num_patterns, len(song.patterns))
    for p in range(num_patterns):
        pat = song.patterns[p] if p < len(song.patterns) else Pattern.empty(64, 4)
        for r in range(64):
            for ch in range(4):
                cell = (pat.cell(r, ch) if r < pat.num_rows and ch < pat.num_channels
                        else Cell())
                period = _note_to_period(cell.note) if cell.note is not None else 0
                instrument = cell.instrument or 0
                effect = cell.effect or 0
                param = cell.param or 0
                b0 = ((instrument & 0xF0)) | ((period >> 8) & 0x0F)
                b1 = period & 0xFF
                b2 = ((instrument & 0x0F) << 4) | (effect & 0x0F)
                b3 = param & 0xFF
                out += bytes([b0, b1, b2, b3])

    # Sample data in instrument order.
    for data in ins_data:
        out += data

    path.write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_mod(path: str | Path) -> Song:
    """Load a 4-channel `.mod`. Raises ValueError on unsupported variants."""
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 1084:
        raise ValueError(f"file too small to be a .mod ({len(data)} bytes)")

    title = data[:20].rstrip(b"\x00").decode("ascii", errors="replace")
    magic = data[1080:1084]
    if magic not in (b"M.K.", b"M!K!", b"FLT4", b"4CHN"):
        # We accept 4-channel variants. 6/8/... chn is a different
        # offset table we don't implement yet.
        raise ValueError(f"unsupported magic {magic!r}; only 4-channel .mod supported")

    # 31 instrument headers
    instruments: list[Instrument] = [Instrument(name="")]  # slot 0 reserved
    sample_sizes: list[int] = []
    for i in range(31):
        off = 20 + i * 30
        hdr = data[off:off + 30]
        name = hdr[:22].rstrip(b"\x00").decode("ascii", errors="replace")
        length_words = struct.unpack(">H", hdr[22:24])[0]
        finetune = hdr[24] & 0x0F
        vol = hdr[25]
        loop_start_words = struct.unpack(">H", hdr[26:28])[0]
        loop_len_words = struct.unpack(">H", hdr[28:30])[0]
        sample_bytes = length_words * 2
        sample_sizes.append(sample_bytes)
        ins = Instrument(
            name=name,
            waveform="sample" if sample_bytes > 0 else "square",
            volume=vol,
            loop_start=loop_start_words * 2,
            loop_end=(loop_start_words + loop_len_words) * 2 if loop_len_words > 1 else 0,
        )
        _ = finetune  # not used in our synth (yet)
        instruments.append(ins)

    # Song length + orders
    song_len = data[950]
    _restart = data[951]
    order = list(data[952:1080])
    num_patterns = max(order[:song_len]) + 1 if song_len else 1

    # Patterns
    patterns: list[Pattern] = []
    ppos = 1084
    for p in range(num_patterns):
        pat = Pattern.empty(64, 4)
        for r in range(64):
            for ch in range(4):
                b = data[ppos:ppos + 4]
                ppos += 4
                period = ((b[0] & 0x0F) << 8) | b[1]
                instrument = (b[0] & 0xF0) | ((b[2] & 0xF0) >> 4)
                effect = b[2] & 0x0F
                param = b[3]
                pat.rows[r][ch] = Cell(
                    note=_period_to_note(period),
                    instrument=instrument if instrument else None,
                    volume=None,
                    effect=effect if (effect or param) else None,
                    param=param if (effect or param) else None,
                )
        patterns.append(pat)

    # Sample data
    for i in range(31):
        size = sample_sizes[i]
        if size <= 0:
            continue
        raw = data[ppos:ppos + size]
        ppos += size
        # signed 8-bit → float -1..1
        samples = [(x if x < 128 else x - 256) / 128.0 for x in raw]
        instruments[i + 1].sample = samples

    song = Song(
        name=title,
        patterns=patterns,
        order=list(order[:song_len]),
        instruments=instruments,
        bpm=125,
        speed=6,
        num_channels=4,
    )
    # Backfill to 32 slots (our UI expects 31 usable)
    while len(song.instruments) < 32:
        song.instruments.append(Instrument(name=f"ins{len(song.instruments):02d}"))
    return song


# Small CLI for `make render-mod`: writes a demo out.
def _demo_write(path: str) -> None:
    from .song import Song
    s = Song.empty(4, 4)
    s.name = "tracker-tui demo"
    # C-E-G-C arpeggio on channel 0, instrument 1 (square)
    notes = [60, 64, 67, 72] * 4
    for i, n in enumerate(notes):
        if i < 64:
            s.patterns[0].rows[i][0] = Cell(note=n, instrument=1, volume=64)
    s.order = [0]
    save_mod(s, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    import sys
    _demo_write(sys.argv[1] if len(sys.argv) > 1 else "demo.mod")
