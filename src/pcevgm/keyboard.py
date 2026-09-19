"""Piano keyboard view of the pitches a track sounds at one instant.

Everything here is a pure function of HuC6280 state, so the view needs no
replay pass of its own and no terminal.

Each white key takes two cells on the lower row. Each black key takes one cell
on the upper row, over the right half of the white key below it. The rest of
the upper row is the top of a white key, which is what a real keyboard shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .huc6280 import FIRST_NOISE_CHANNEL, midi_number

LOW_MIDI = 24  # C1; 99% of the pitches in the sample rips sit above E1
OCTAVES = 5
SEMITONES = OCTAVES * 12
WHITE_STEPS = (0, 2, 4, 5, 7, 9, 11)
BLACK_STEPS = (1, 3, 6, 8, 10)
CELLS_PER_WHITE = 2
OCTAVE_CELLS = len(WHITE_STEPS) * CELLS_PER_WHITE  # 14
WIDTH = OCTAVES * OCTAVE_CELLS  # 70

# Below this a channel is parked, not playing. At -60 dB the display fills with
# channels sitting on divider 0 at 27 Hz.
SILENCE_DB = -40.0

UPPER, LOWER = 0, 1
SEPARATOR = "▏"  # the line between two white keys
BELOW_MARK = "<"
ABOVE_MARK = ">"
MORE_MARK = "+"  # more channels on one key than the key has cells


@dataclass
class Cell:
    """One character cell of the keyboard."""

    char: str
    black: bool  # part of a black key
    channel: Optional[int] = None  # the channel sounding this key, if any


def _build_keys():
    """semitone offset -> (row, first cell, cell count), plus the black cells."""
    keys = {}
    black_cells = set()
    column = 0
    for octave in range(OCTAVES):
        for step in WHITE_STEPS:
            keys[octave * 12 + step] = (LOWER, column, CELLS_PER_WHITE)
            if step + 1 in BLACK_STEPS:
                keys[octave * 12 + step + 1] = (UPPER, column + 1, 1)
                black_cells.add(column + 1)
            column += CELLS_PER_WHITE
    return keys, frozenset(black_cells)


KEYS, BLACK_CELLS = _build_keys()


def sounding(state, floor_db: float = SILENCE_DB) -> dict:
    """channel -> semitone offset from LOW_MIDI, for channels making a pitch.

    An offset outside 0 to SEMITONES means the pitch is off the keyboard.
    """
    out = {}
    for index, channel in enumerate(state.channels):
        if not channel.enabled or channel.dda:
            continue
        if index >= FIRST_NOISE_CHANNEL and channel.noise_enabled:
            continue
        levels = channel.levels_db(state.master_left, state.master_right)
        if levels is None or max(levels) < floor_db:
            continue
        midi = midi_number(channel.frequency_hz(state.clock))
        if midi is not None:
            out[index] = midi - LOW_MIDI
    return out


def unpitched(state) -> list:
    """Enabled channels that make no pitch, as (channel, reason)."""
    out = []
    for index, channel in enumerate(state.channels):
        if not channel.enabled:
            continue
        if channel.dda:
            out.append((index, "dda"))
        elif index >= FIRST_NOISE_CHANNEL and channel.noise_enabled:
            out.append((index, "noise"))
    return out


def render(state, floor_db: float = SILENCE_DB):
    """The upper and lower cell rows for the current state."""
    upper = [Cell(" ", column in BLACK_CELLS) for column in range(WIDTH)]
    lower = [Cell(" ", False) for _ in range(WIDTH)]
    for column in range(0, WIDTH, CELLS_PER_WHITE):
        lower[column].char = SEPARATOR

    by_key = {}
    for channel, offset in sorted(sounding(state, floor_db).items()):
        by_key.setdefault(offset, []).append(channel)

    for offset, channels in sorted(by_key.items()):
        if offset not in KEYS:
            cell = lower[0] if offset < 0 else lower[WIDTH - 1]
            cell.char = BELOW_MARK if offset < 0 else ABOVE_MARK
            cell.channel = channels[0]
            continue
        row, start, count = KEYS[offset]
        cells = upper if row == UPPER else lower
        # A white key has two cells, so it can name two channels at once.
        for step in range(count):
            cell = cells[start + step]
            cell.channel = channels[step] if step < len(channels) else channels[0]
            if step < len(channels):
                cell.char = str(channels[step])
        if len(channels) > count:
            cells[start + count - 1].char = MORE_MARK
    return upper, lower


def labels() -> str:
    """The octave marks that sit under the keys."""
    row = [" "] * WIDTH
    for octave in range(OCTAVES):
        text = f"C{LOW_MIDI // 12 - 1 + octave}"
        for offset, character in enumerate(text):
            row[octave * OCTAVE_CELLS + offset] = character
    return "".join(row)
