"""Register state for the HuC6280 PSG, the PC Engine sound chip.

This models the register file only. It does not generate audio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

NUM_CHANNELS = 6
WAVE_LENGTH = 32
DEFAULT_CLOCK = 3_579_545
FIRST_NOISE_CHANNEL = 4  # noise exists on channels 4 and 5 only

# Register addresses. The chip maps these at $0800 to $0809.
REG_CHANNEL_SELECT = 0x00
REG_MASTER_BALANCE = 0x01
REG_FREQ_LOW = 0x02
REG_FREQ_HIGH = 0x03
REG_CONTROL = 0x04
REG_BALANCE = 0x05
REG_WAVE_DATA = 0x06
REG_NOISE = 0x07
REG_LFO_FREQ = 0x08
REG_LFO_CONTROL = 0x09

REGISTER_NAMES = {
    REG_CHANNEL_SELECT: "channel select",
    REG_MASTER_BALANCE: "master balance",
    REG_FREQ_LOW: "freq low",
    REG_FREQ_HIGH: "freq high",
    REG_CONTROL: "control",
    REG_BALANCE: "balance",
    REG_WAVE_DATA: "wave data",
    REG_NOISE: "noise",
    REG_LFO_FREQ: "LFO freq",
    REG_LFO_CONTROL: "LFO control",
}

# Field widths inside a register byte.
REGISTER_MASK = 0x0F  # the chip decodes 4 address bits
BYTE_MASK = 0xFF
NIBBLE_MASK = 0x0F
CHANNEL_MASK = 0x07  # REG_CHANNEL_SELECT holds 3 bits
FREQ_HIGH_MASK = 0x0F  # REG_FREQ_HIGH holds the top 4 of 12 bits
FREQ_LOW_MASK = 0x00FF
FREQ_HIGH_SHIFT = 8
AMPLITUDE_MASK = 0x1F
SAMPLE_MASK = 0x1F  # wave and DDA samples are 5 bits
CONTROL_ENABLE = 0x80
CONTROL_DDA = 0x40
NOISE_ENABLE = 0x80
NOISE_FREQ_MASK = 0x1F
LFO_DISABLE = 0x80  # 1 disables the LFO and resets the source channel
LFO_DEPTH_MASK = 0x03
# A depth code adds the source channel output times this factor to the pitch.
LFO_DEPTH_FACTORS = (0, 1, 16, 256)

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def lfo_depth_factor(depth: int) -> int:
    """The pitch multiplier a LFO depth code selects."""
    return LFO_DEPTH_FACTORS[depth & LFO_DEPTH_MASK]
BLOCKS = "▁▂▃▄▅▆▇█"  # 8 fill levels, for the one-row sparkline
HBLOCKS = "▏▎▍▌▋▊▉█"  # the same 8 steps lying down, for meters
# A braille cell holds two dot columns and four dot rows.
BRAILLE_BASE = 0x2800
BRAILLE_DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))  # [column][row]
BRAILLE_COLUMNS = 2
BRAILLE_ROWS = 4
WAVE_ROWS = 3  # character rows a plot uses
PLOT_LEVELS = WAVE_ROWS * BRAILLE_ROWS  # 12

# Amplitude steps are 1.5 dB. Balance steps are 3.0 dB, so two amplitude units.
BALANCE_WEIGHT = 2
MAX_VOLUME_STEPS = AMPLITUDE_MASK + 2 * BALANCE_WEIGHT * NIBBLE_MASK  # 91
DB_PER_STEP = 1.5
METER_FLOOR_DB = -60.0  # where a level meter reads empty


@dataclass
class Channel:
    frequency: int = 0  # 12-bit divider
    enabled: bool = False
    dda: bool = False  # direct D/A mode
    amplitude: int = 0  # 5-bit
    balance_left: int = 0  # 4-bit
    balance_right: int = 0  # 4-bit
    waveform: list = field(default_factory=lambda: [0] * WAVE_LENGTH)
    wave_index: int = 0
    dda_sample: int = 0
    noise_enabled: bool = False
    noise_frequency: int = 0  # 5-bit
    writes: int = 0

    def frequency_hz(self, clock: int) -> float:
        """Wave repeat rate. The chip plays 32 samples per period."""
        divider = self.frequency or 4096
        return clock / (WAVE_LENGTH * divider)

    def level_steps(self, master_left: int, master_right: int):
        """Volume per side in register steps, 0 to MAX_VOLUME_STEPS.

        Returns None when the channel is off.
        """
        if not self.enabled:
            return None
        left = self.amplitude + BALANCE_WEIGHT * (self.balance_left + master_left)
        right = self.amplitude + BALANCE_WEIGHT * (self.balance_right + master_right)
        return (
            max(0, min(MAX_VOLUME_STEPS, left)),
            max(0, min(MAX_VOLUME_STEPS, right)),
        )

    def levels_db(self, master_left: int, master_right: int):
        """Attenuation per side in dB, or None when the channel is off."""
        steps = self.level_steps(master_left, master_right)
        if steps is None:
            return None
        # The + 0.0 turns -0.0 into 0.0 at full volume.
        return tuple(-DB_PER_STEP * (MAX_VOLUME_STEPS - step) + 0.0 for step in steps)


class HuC6280State:
    """Applies register writes and holds the resulting chip state."""

    def __init__(self, clock: int = DEFAULT_CLOCK) -> None:
        self.clock = clock or DEFAULT_CLOCK
        self.channels = [Channel() for _ in range(NUM_CHANNELS)]
        self.selected = 0
        self.master_left = 0
        self.master_right = 0
        self.lfo_frequency = 0
        self.lfo_control = 0  # the raw register byte
        self.lfo_enabled = True  # bit 7 clear
        self.lfo_depth = 0  # bits 1 and 0
        self.writes = 0

    def write(self, register: int, value: int) -> None:
        register &= REGISTER_MASK
        value &= BYTE_MASK
        self.writes += 1

        if register == REG_CHANNEL_SELECT:
            self.selected = value & CHANNEL_MASK
            return
        if register == REG_MASTER_BALANCE:
            self.master_left = (value >> 4) & NIBBLE_MASK
            self.master_right = value & NIBBLE_MASK
            return
        if register == REG_LFO_FREQ:
            self.lfo_frequency = value
            return
        if register == REG_LFO_CONTROL:
            self.lfo_control = value
            self.lfo_enabled = not value & LFO_DISABLE
            self.lfo_depth = value & LFO_DEPTH_MASK
            return
        if self.selected >= NUM_CHANNELS:
            return  # channel 6 and 7 do not exist

        channel = self.channels[self.selected]
        channel.writes += 1
        if register == REG_FREQ_LOW:
            channel.frequency = (channel.frequency & ~FREQ_LOW_MASK) | value
        elif register == REG_FREQ_HIGH:
            high = (value & FREQ_HIGH_MASK) << FREQ_HIGH_SHIFT
            channel.frequency = (channel.frequency & FREQ_LOW_MASK) | high
        elif register == REG_CONTROL:
            channel.enabled = bool(value & CONTROL_ENABLE)
            channel.dda = bool(value & CONTROL_DDA)
            channel.amplitude = value & AMPLITUDE_MASK
            if not channel.enabled:
                channel.wave_index = 0  # the chip resets the wave pointer
        elif register == REG_BALANCE:
            channel.balance_left = (value >> 4) & NIBBLE_MASK
            channel.balance_right = value & NIBBLE_MASK
        elif register == REG_WAVE_DATA:
            if channel.dda:
                channel.dda_sample = value & SAMPLE_MASK
            else:
                channel.waveform[channel.wave_index] = value & SAMPLE_MASK
                channel.wave_index = (channel.wave_index + 1) % WAVE_LENGTH
        elif register == REG_NOISE:
            if self.selected >= FIRST_NOISE_CHANNEL:
                channel.noise_enabled = bool(value & NOISE_ENABLE)
                channel.noise_frequency = value & NOISE_FREQ_MASK


def describe_write(register: int, value: int, selected: int) -> str:
    """Text for one register write, given the channel selected before it."""
    register &= REGISTER_MASK
    value &= BYTE_MASK
    if register == REG_CHANNEL_SELECT:
        return f"select channel {value & CHANNEL_MASK}"
    if register == REG_MASTER_BALANCE:
        return f"master balance L={value >> 4:X} R={value & NIBBLE_MASK:X}"
    if register == REG_LFO_FREQ:
        return f"LFO freq {value}"
    if register == REG_LFO_CONTROL:
        depth = value & LFO_DEPTH_MASK
        state = "off" if value & LFO_DISABLE else "on "
        return f"LFO {state} depth {depth} (x{lfo_depth_factor(depth)})"

    prefix = f"ch{selected}"
    if register == REG_FREQ_LOW:
        return f"{prefix} freq low 0x{value:02X}"
    if register == REG_FREQ_HIGH:
        return f"{prefix} freq high 0x{value & FREQ_HIGH_MASK:X}"
    if register == REG_CONTROL:
        state = "on " if value & CONTROL_ENABLE else "off"
        dda = " dda" if value & CONTROL_DDA else ""
        return f"{prefix} {state} amp={value & AMPLITUDE_MASK}{dda}"
    if register == REG_BALANCE:
        return f"{prefix} balance L={value >> 4:X} R={value & NIBBLE_MASK:X}"
    if register == REG_WAVE_DATA:
        return f"{prefix} wave/dda {value & SAMPLE_MASK}"
    if register == REG_NOISE:
        state = "on" if value & NOISE_ENABLE else "off"
        return f"{prefix} noise {state} freq={value & NOISE_FREQ_MASK}"
    name = REGISTER_NAMES.get(register, "unused")
    return f"{prefix} {name} reg 0x{register:02X} = 0x{value:02X}"


AUDIBLE_HZ = 20000


def midi_value(hz: float):
    """Pitch as a fractional MIDI note number, or None when out of range."""
    if hz <= 0 or hz > AUDIBLE_HZ:
        return None
    return 69 + 12 * math.log2(hz / 440.0)


def midi_number(hz: float):
    """Nearest MIDI note number, or None when out of range."""
    value = midi_value(hz)
    if value is None:
        return None
    nearest = round(value)
    return nearest if 0 <= nearest < 128 else None


def note_text(hz: float) -> str:
    """Nearest note plus cents, for example 'A4+03'."""
    value = midi_value(hz)
    nearest = midi_number(hz)
    if value is None or nearest is None:
        return "--"
    cents = int(round((value - nearest) * 100))
    return f"{NOTE_NAMES[nearest % 12]}{nearest // 12 - 1}{cents:+03d}"


def sparkline(wave) -> str:
    """Draw a wave table as one character per sample."""
    levels = len(BLOCKS)
    return "".join(BLOCKS[min(levels - 1, value * levels // WAVE_LENGTH)] for value in wave)


def plot_width(samples: int) -> int:
    """Character columns a plot of this many samples takes."""
    return -(-samples // BRAILLE_COLUMNS)


def wave_rows(wave):
    """Draw a sample series as a connected line, WAVE_ROWS characters tall.

    One braille character covers two samples and four dot rows, so WAVE_ROWS of
    3 resolves PLOT_LEVELS steps in half the width a filled bar would need. The
    dots between two neighbouring samples are lit as well, so the line stays
    joined instead of reading as scattered points. Row 0 is the top.
    """
    grid = [[0] * plot_width(len(wave)) for _ in range(WAVE_ROWS)]
    previous = None
    for column, value in enumerate(wave):
        level = min(PLOT_LEVELS - 1, value * PLOT_LEVELS // (SAMPLE_MASK + 1))
        low, high = level, level
        if previous is not None:
            low, high = min(level, previous), max(level, previous)
        previous = level
        for lit in range(low, high + 1):
            line = PLOT_LEVELS - 1 - lit  # counting down from the top
            grid[line // BRAILLE_ROWS][column // BRAILLE_COLUMNS] |= BRAILLE_DOTS[
                column % BRAILLE_COLUMNS
            ][line % BRAILLE_ROWS]
    return ["".join(chr(BRAILLE_BASE + cell) for cell in row) for row in grid]


def db_fraction(db: float) -> float:
    """Map a dB level onto 0 to 1 for a meter, reading empty at METER_FLOOR_DB.

    A meter linear in register steps would sit near full across the whole
    musical range, because the chip spans about 136 dB.
    """
    return max(0.0, min(1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB))


def meter(fraction: float, width: int) -> str:
    """A horizontal bar `width` characters wide, filled to `fraction`.

    Partial blocks give each character 8 steps, so a narrow bar still moves.
    """
    steps = len(HBLOCKS)
    filled = round(max(0.0, min(1.0, fraction)) * width * steps)
    whole, part = divmod(filled, steps)
    bar = HBLOCKS[-1] * whole + (HBLOCKS[part - 1] if part else "")
    return bar.ljust(width)
