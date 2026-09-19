"""Note, envelope and instrument analysis.

This driver family rarely gates a note. It leaves a channel enabled and
rewrites pitch and amplitude every video frame, so a note start has to be
inferred. The rule here fires on three signals, debounced to one frame:

- the channel gates on while its amplitude is above zero
- the pitch reloads by more than a semitone
- the amplitude jumps up by ATTACK_STEPS or more

Writes are grouped into instants, meaning every write sharing one sample time.
The driver sets the pitch low byte, the pitch high byte and the control byte in
one instant, so judging per instant avoids reacting to a half-written pitch.

The output is a heuristic. There is no reference note list for these files, so
treat the note count as an estimate, not a measurement.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .huc6280 import (
    DEFAULT_CLOCK,
    note_text,
    FIRST_NOISE_CHANNEL,
    NUM_CHANNELS,
    HuC6280State,
)
from .player import HUC6280_WRITE
from .vgm import VgmFile
from .waves import extract as extract_waves
from .waves import names_by_samples, numbered_names

FRAME = 735  # one NTSC video frame, the rate the driver writes at
SEMITONE_RATIO = 2 ** (1 / 12) - 1  # 5.9% of the divider
ATTACK_STEPS = 4  # amplitude steps, about 6 dB
DEBOUNCE = FRAME  # no two onsets on one channel inside a frame
ENVELOPE_MAX_STEPS = 32  # a cap, so one long note cannot dominate the table
UNKNOWN_WAVE = "wave  --"


@dataclass
class Note:
    channel: int
    start: int  # sample time of the onset
    end: int
    divider: int  # the 12-bit pitch divider at the onset
    wave: bytes
    amps: list = field(default_factory=list)  # (sample, amplitude) at each change
    envelope: tuple = ()  # the amplitude steps, after prefix merging
    instrument: int = -1

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class Analysis:
    notes: list
    envelopes: list  # canonical envelope per id
    instruments: list  # (wave name, envelope id) per id
    wave_names: dict
    clock: int = DEFAULT_CLOCK

    def __post_init__(self):
        self._per_channel = [[] for _ in range(NUM_CHANNELS)]
        for note in self.notes:
            self._per_channel[note.channel].append(note)
        self._starts = [[n.start for n in row] for row in self._per_channel]
        self.envelope_names = numbered_names("env", len(self.envelopes))
        self.instrument_names = numbered_names("inst", len(self.instruments))

    def channel_notes(self, channel: int) -> list:
        return self._per_channel[channel]

    def note_at(self, channel: int, sample: int):
        """The note sounding on a channel at a sample time, or None."""
        starts = self._starts[channel]
        index = bisect.bisect_right(starts, sample) - 1
        if index < 0:
            return None
        note = self._per_channel[channel][index]
        return note if note.start <= sample <= note.end else None

    def instrument_at(self, channel: int, sample: int):
        note = self.note_at(channel, sample)
        return None if note is None else note.instrument

    def pitch_text(self, note: Note) -> str:
        divider = note.divider or 4096
        return note_text(self.clock / (32 * divider))


def instants(commands):
    """Yield (sample, [(register, value), ...]) for each distinct sample time."""
    pending = []
    at = 0
    for command in commands:
        if command.opcode == HUC6280_WRITE and len(command.operands) == 2:
            if command.sample != at and pending:
                yield at, pending
                pending = []
            at = command.sample
            pending.append((command.operands[0], command.operands[1]))
    if pending:
        yield at, pending


def _pitched(channel, index: int) -> bool:
    if not channel.enabled or channel.dda:
        return False
    return not (index >= FIRST_NOISE_CHANNEL and channel.noise_enabled)


def detect(vgm: VgmFile) -> list:
    """Every note the detector finds, in start order."""
    state = HuC6280State(vgm.header.huc6280_clock or DEFAULT_CLOCK)
    previous = [(False, 0, 0)] * NUM_CHANNELS
    live = [None] * NUM_CHANNELS
    last_onset = [-DEBOUNCE * 2] * NUM_CHANNELS
    notes = []

    def close(index: int, at: int) -> None:
        note = live[index]
        if note is not None:
            note.end = max(at, note.start)
            notes.append(note)
            live[index] = None

    at = 0
    for at, writes in instants(vgm.commands):
        for register, value in writes:
            state.write(register, value)
        for index in range(NUM_CHANNELS):
            channel = state.channels[index]
            was_on, was_amp, was_divider = previous[index]
            previous[index] = (channel.enabled, channel.amplitude, channel.frequency)

            if not _pitched(channel, index):
                close(index, at)
                continue

            moved = abs(channel.frequency - was_divider) / max(1, was_divider)
            onset = (
                (not was_on and channel.amplitude > 0)
                or (moved > SEMITONE_RATIO and channel.amplitude > 0)
                or (channel.amplitude - was_amp >= ATTACK_STEPS)
            )
            if onset and at - last_onset[index] >= DEBOUNCE:
                close(index, at)
                last_onset[index] = at
                live[index] = Note(
                    channel=index,
                    start=at,
                    end=at,
                    divider=channel.frequency,
                    wave=bytes(channel.waveform),
                    amps=[(at, channel.amplitude)],
                )
            elif live[index] is not None and channel.amplitude != was_amp:
                live[index].amps.append((at, channel.amplitude))
                if channel.amplitude == 0:
                    close(index, at)

    for index in range(NUM_CHANNELS):
        close(index, at)
    notes.sort(key=lambda note: (note.start, note.channel))
    return notes


def envelope_of(note: Note) -> tuple:
    """The amplitudes the driver wrote for this note, in order.

    Resampling onto a frame grid looked tempting, but the onset does not sit on
    the driver's own frame phase, so a step lands a frame early or late and one
    envelope splits into several. The write sequence carries no such jitter.
    """
    return tuple(amplitude for _, amplitude in note.amps[:ENVELOPE_MAX_STEPS])


def merge_prefixes(shapes) -> dict:
    """Map each envelope onto the longest envelope it is a prefix of.

    A note cut short is a prefix of the full envelope, so merging prefixes
    stops one envelope table entry looking like a dozen.
    """
    canonical = []
    mapping = {}
    for shape in sorted(set(shapes), key=len, reverse=True):
        for full in canonical:
            if full[: len(shape)] == shape:
                mapping[shape] = full
                break
        else:
            canonical.append(shape)
            mapping[shape] = shape
    return mapping


def analyse(vgm: VgmFile) -> Analysis:
    """Notes, the envelopes they use, and the instruments those make."""
    notes = detect(vgm)
    wave_names = names_by_samples(extract_waves(vgm))

    merged = merge_prefixes(envelope_of(note) for note in notes)
    envelopes, envelope_ids = [], {}
    instruments, instrument_ids = [], {}
    for note in notes:
        shape = merged[envelope_of(note)]
        note.envelope = shape
        if shape not in envelope_ids:
            envelope_ids[shape] = len(envelopes)
            envelopes.append(shape)
        key = (note.wave, envelope_ids[shape])
        if key not in instrument_ids:
            instrument_ids[key] = len(instruments)
            instruments.append((wave_names.get(note.wave, UNKNOWN_WAVE), key[1]))
        note.instrument = instrument_ids[key]

    clock = vgm.header.huc6280_clock or DEFAULT_CLOCK
    return Analysis(notes, envelopes, instruments, wave_names, clock)
