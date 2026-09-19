"""Piano keyboard view of the pitches a track sounds at one instant.

Everything here is a pure function of HuC6280 state, so the view needs no
replay pass of its own and no terminal.

Each layer of keys is KEY_ROWS character rows tall. A white key is two cells
wide on the lower layer, so it holds four cells. A black key is one cell wide
on the upper layer, over the right half of the white key below it, so it holds
two. The rest of the upper layer is the top of a white key, which is what a
real keyboard shows. The cells of one key are shared out between the channels
sounding it, so several voices on one note stay readable.
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
KEY_ROWS = 2  # character rows per layer of keys
TOTAL_ROWS = 2 * KEY_ROWS  # the black layer above the white layer
FIRST_WHITE_ROW = KEY_ROWS

# Below this a channel is parked, not playing. At -60 dB the display fills with
# channels sitting on divider 0 at 27 Hz.
SILENCE_DB = -40.0

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
    """semitone offset -> its (row, column) cells, plus the black key columns.

    Cells run left to right, then top to bottom.
    """
    keys = {}
    black_cells = set()
    column = 0
    for octave in range(OCTAVES):
        for step in WHITE_STEPS:
            keys[octave * 12 + step] = [
                (row, column + cell)
                for row in range(FIRST_WHITE_ROW, TOTAL_ROWS)
                for cell in range(CELLS_PER_WHITE)
            ]
            if step + 1 in BLACK_STEPS:
                keys[octave * 12 + step + 1] = [
                    (row, column + 1) for row in range(KEY_ROWS)
                ]
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


def sample_channels(state) -> list:
    """Enabled channels playing a sample through DDA instead of a pitch.

    Noise channels are left out. The channel table already reports their noise
    register, and naming them beside the board only adds clutter.
    """
    return [
        index
        for index, channel in enumerate(state.channels)
        if channel.enabled and channel.dda
    ]


def _blank_rows():
    rows = []
    for row in range(TOTAL_ROWS):
        if row < FIRST_WHITE_ROW:
            rows.append([Cell(" ", column in BLACK_CELLS) for column in range(WIDTH)])
            continue
        cells = [Cell(" ", False) for _ in range(WIDTH)]
        for column in range(0, WIDTH, CELLS_PER_WHITE):
            cells[column].char = SEPARATOR
        rows.append(cells)
    return rows


def _fill_key(cells: list, channels: list) -> None:
    """Share a key's cells out between the channels sounding it."""
    count = len(cells)
    if len(channels) > count:
        for index in range(count - 1):
            cells[index].channel = channels[index]
            cells[index].char = str(channels[index])
        cells[-1].channel = channels[count - 1]
        cells[-1].char = MORE_MARK
        return
    previous = None
    for index, cell in enumerate(cells):
        channel = channels[index * len(channels) // count]
        cell.channel = channel
        if channel != previous:  # name each channel once, at its first cell
            cell.char = str(channel)
            previous = channel


def render(state, floor_db: float = SILENCE_DB):
    """One list of cells per character row, top to bottom."""
    rows = _blank_rows()

    by_key = {}
    for channel, offset in sorted(sounding(state, floor_db).items()):
        by_key.setdefault(offset, []).append(channel)

    for offset, channels in sorted(by_key.items()):
        if offset not in KEYS:
            edge = 0 if offset < 0 else WIDTH - 1
            cell = rows[FIRST_WHITE_ROW][edge]
            cell.char = BELOW_MARK if offset < 0 else ABOVE_MARK
            cell.channel = channels[0]
            continue
        _fill_key([rows[row][column] for row, column in KEYS[offset]], channels)
    return rows


def labels() -> str:
    """The octave marks that sit under the keys."""
    row = [" "] * WIDTH
    for octave in range(OCTAVES):
        text = f"C{LOW_MIDI // 12 - 1 + octave}"
        for offset, character in enumerate(text):
            row[octave * OCTAVE_CELLS + offset] = character
    return "".join(row)
