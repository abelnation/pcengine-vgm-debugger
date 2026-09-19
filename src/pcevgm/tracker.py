"""Tracker-style grid of what every channel does on each driver tick.

The driver writes once per video frame and spreads one tick's writes across a
few sample times, so a tick is a run of write instants closer together than
half a frame. One tick is one row.

The amplitude column is read straight from the chip. The note and instrument
columns come from the note detector, so they carry its guesswork.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import NamedTuple

from .huc6280 import FIRST_NOISE_CHANNEL, NUM_CHANNELS, midi_number
from .notes import FRAME, Analysis, analyse, instants
from .player import Timeline
from .vgm import SAMPLE_RATE, VgmFile

TICK_GAP = FRAME // 2  # instants closer than this belong to one tick
NOTE_NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
NO_NOTE = "..."  # sounding, but the detector found no new note here
NO_VALUE = ".."  # no note starts on this row, so no instrument either
# A channel prints the row its amplitude reaches zero, then leaves its cell
# empty until it sounds again, the way a tracker does.
BLANK_NOTE = "   "
BLANK_VALUE = "  "

ROW_LABEL = 5  # digits in the row number
CELL_NARROW = len("C-5 1F")  # note and amplitude
CELL_WIDE = len("C-5 1F 00")  # note, amplitude and instrument
CELL_NOISE = len("1F 1B")  # noise frequency and amplitude
NOISE_CHANNELS = tuple(range(FIRST_NOISE_CHANNEL, NUM_CHANNELS))


@dataclass
class Cell:
    """One channel on one row, in the order the cell prints."""

    note: str
    amplitude: str
    instrument: str
    onset: bool = False  # a note starts here
    level: int = -1  # the amplitude as a number, -1 when the cell is empty


@dataclass
class NoiseCell:
    """One noise channel on one row. Noise has a frequency, not a pitch."""

    frequency: str
    amplitude: str
    onset: bool = False
    level: int = -1


@dataclass
class Row:
    index: int
    sample: int
    cells: list
    noise: list

    @property
    def time_text(self) -> str:
        seconds = self.sample / SAMPLE_RATE
        minutes = int(seconds // 60)
        return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def ticks(vgm: VgmFile) -> list:
    """The sample time each driver tick starts at."""
    out = []
    for at, _ in instants(vgm.commands):
        if not out or at - out[-1] > TICK_GAP:
            out.append(at)
    return out


def note_name(hz: float) -> str:
    """Tracker spelling of a pitch, for example 'C-5' or 'F#2'."""
    midi = midi_number(hz)
    if midi is None:
        return NO_NOTE
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def build(vgm: VgmFile, analysis: Analysis = None) -> list:
    """The whole grid, one Row per tick."""
    analysis = analysis or analyse(vgm)
    times = ticks(vgm)
    onsets = {}
    for note in analysis.notes:
        index = bisect.bisect_right(times, note.start) - 1
        if index >= 0:
            onsets[(index, note.channel)] = note

    timeline = Timeline(vgm)
    rows = []
    previous_noise = {channel: None for channel in NOISE_CHANNELS}
    live = [False] * NUM_CHANNELS  # the tone cell printed something last row
    noise_live = {channel: False for channel in NOISE_CHANNELS}
    for index, at in enumerate(times):
        timeline.seek_sample(at)
        state = timeline.state
        cells = []
        for channel_index in range(NUM_CHANNELS):
            channel = state.channels[channel_index]
            playing = channel.enabled and not (
                channel_index >= FIRST_NOISE_CHANNEL and channel.noise_enabled
            )
            if not playing or (channel.amplitude == 0 and not live[channel_index]):
                cells.append(Cell(BLANK_NOTE, BLANK_VALUE, BLANK_VALUE))
                live[channel_index] = False
                continue
            # Zero prints once, as the row the note lets go.
            live[channel_index] = channel.amplitude > 0
            note = onsets.get((index, channel_index))
            cells.append(
                Cell(
                    note_name(channel.frequency_hz(state.clock)) if note else NO_NOTE,
                    f"{channel.amplitude:02X}",
                    f"{note.instrument:02d}" if note else NO_VALUE,
                    onset=note is not None,
                    level=channel.amplitude,
                )
            )
        noise = []
        for channel_index in NOISE_CHANNELS:
            channel = state.channels[channel_index]
            active = channel.enabled and channel.noise_enabled
            quiet = channel.amplitude == 0
            if not active or (quiet and not noise_live[channel_index]):
                noise.append(NoiseCell(BLANK_VALUE, BLANK_VALUE))
                noise_live[channel_index] = False
                previous_noise[channel_index] = None
                continue
            setting = (channel.noise_enabled, channel.noise_frequency)
            started = not quiet and setting != previous_noise[channel_index]
            previous_noise[channel_index] = None if quiet else setting
            noise_live[channel_index] = not quiet
            noise.append(
                NoiseCell(
                    f"{channel.noise_frequency:02X}" if started else NO_VALUE,
                    f"{channel.amplitude:02X}",
                    onset=started,
                    level=channel.amplitude,
                )
            )
        rows.append(Row(index, at, cells, noise))
    return rows


def format_cell(cell: Cell, wide: bool) -> str:
    """Note, amplitude, then the instrument. The narrow cell drops the last."""
    if wide:
        return f"{cell.note} {cell.amplitude} {cell.instrument}"
    return f"{cell.note} {cell.amplitude}"


def format_noise(cell: NoiseCell) -> str:
    return f"{cell.frequency} {cell.amplitude}"


def format_row(row: Row, wide: bool = False) -> str:
    cells = " ".join(format_cell(cell, wide) for cell in row.cells)
    noise = " ".join(format_noise(cell) for cell in row.noise)
    return f"{row.index:{ROW_LABEL}d} {cells} {noise}"


class Segment(NamedTuple):
    """One field of a row, with what a caller needs to colour it."""

    text: str
    onset: bool
    level: int  # the amplitude, or -1 where there is none


def row_segments(row: Row, wide: bool = False) -> list:
    """Every field of a row, in print order.

    Joining the texts with one space rebuilds format_row, so a caller can give
    each field its own colour without measuring column offsets.
    """
    started = any(cell.onset for cell in row.cells + row.noise)
    out = [Segment(f"{row.index:{ROW_LABEL}d}", started, -1)]
    out += [Segment(format_cell(c, wide), c.onset, c.level) for c in row.cells]
    out += [Segment(format_noise(c), c.onset, c.level) for c in row.noise]
    return out


def format_header(wide: bool = False) -> str:
    width = CELL_WIDE if wide else CELL_NARROW
    names = " ".join(f"{'ch' + str(c):<{width}}" for c in range(NUM_CHANNELS))
    noise = " ".join(f"{'n' + str(c):<{CELL_NOISE}}" for c in NOISE_CHANNELS)
    return f"{'row':>{ROW_LABEL}} {names} {noise}"


def panel_width(wide: bool = False) -> int:
    cell = CELL_WIDE if wide else CELL_NARROW
    tone = NUM_CHANNELS * cell + (NUM_CHANNELS - 1)
    noise = len(NOISE_CHANNELS) * CELL_NOISE + (len(NOISE_CHANNELS) - 1)
    return ROW_LABEL + 1 + tone + 1 + noise


def row_at(rows: list, sample: int) -> int:
    """Index of the row covering a sample time."""
    starts = [row.sample for row in rows]
    return max(0, bisect.bisect_right(starts, sample) - 1)


def dump(vgm: VgmFile, rows: list, path: str) -> None:
    """Write the whole grid to a text file."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"source  {vgm.path}\n")
        handle.write(f"rows    {len(rows)} ticks, one per video frame\n")
        handle.write("cell    note, amplitude in hex, instrument\n")
        handle.write(f"        {NO_NOTE} sounding with no new note,"
                     " an empty cell is silent\n")
        handle.write("noise   n4 and n5 hold the noise frequency and amplitude,"
                     " both in hex\n\n")
        handle.write("time      " + format_header(wide=True) + "\n")
        for row in rows:
            handle.write(f"{row.time_text}  {format_row(row, wide=True)}\n")
