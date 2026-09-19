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

from .huc6280 import FIRST_NOISE_CHANNEL, NUM_CHANNELS, midi_number
from .notes import FRAME, Analysis, analyse, instants
from .player import Timeline
from .vgm import SAMPLE_RATE, VgmFile

TICK_GAP = FRAME // 2  # instants closer than this belong to one tick
NOTE_NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
NO_NOTE = "..."  # sounding, but the detector found no new note here
SILENT = "---"
NO_VALUE = ".."

ROW_LABEL = 5  # digits in the row number
CELL_NARROW = len("C-5 1F")  # note and amplitude
CELL_WIDE = len("C-5 1F 00")  # note, amplitude and instrument


@dataclass
class Cell:
    note: str
    instrument: str
    amplitude: str


@dataclass
class Row:
    index: int
    sample: int
    cells: list

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
    for index, at in enumerate(times):
        timeline.seek_sample(at)
        state = timeline.state
        cells = []
        for channel_index in range(NUM_CHANNELS):
            channel = state.channels[channel_index]
            silent = not channel.enabled or (
                channel_index >= FIRST_NOISE_CHANNEL and channel.noise_enabled
            )
            if silent:
                cells.append(Cell(SILENT, NO_VALUE, NO_VALUE))
                continue
            note = onsets.get((index, channel_index))
            cells.append(
                Cell(
                    note_name(channel.frequency_hz(state.clock)) if note else NO_NOTE,
                    f"{note.instrument:02d}" if note else NO_VALUE,
                    f"{channel.amplitude:02X}",
                )
            )
        rows.append(Row(index, at, cells))
    return rows


def format_cell(cell: Cell, wide: bool) -> str:
    """Note, amplitude, then the instrument. The narrow cell drops the last."""
    if wide:
        return f"{cell.note} {cell.amplitude} {cell.instrument}"
    return f"{cell.note} {cell.amplitude}"


def format_row(row: Row, wide: bool = False) -> str:
    cells = " ".join(format_cell(cell, wide) for cell in row.cells)
    return f"{row.index:{ROW_LABEL}d} {cells}"


def format_header(wide: bool = False) -> str:
    width = CELL_WIDE if wide else CELL_NARROW
    names = " ".join(f"{'ch' + str(c):<{width}}" for c in range(NUM_CHANNELS))
    return f"{'row':>{ROW_LABEL}} {names}"


def panel_width(wide: bool = False) -> int:
    cell = CELL_WIDE if wide else CELL_NARROW
    return ROW_LABEL + 1 + NUM_CHANNELS * cell + (NUM_CHANNELS - 1)


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
        handle.write(f"        {NO_NOTE} sounding with no new note, {SILENT} silent\n\n")
        handle.write("time      " + format_header(wide=True) + "\n")
        for row in rows:
            handle.write(f"{row.time_text}  {format_row(row, wide=True)}\n")
