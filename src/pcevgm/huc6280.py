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

REGISTER_NAMES = {
    0x00: "channel select",
    0x01: "master balance",
    0x02: "freq low",
    0x03: "freq high",
    0x04: "control",
    0x05: "balance",
    0x06: "wave data",
    0x07: "noise",
    0x08: "LFO freq",
    0x09: "LFO control",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Amplitude steps are 1.5 dB. Balance steps are 3.0 dB, so two amplitude units.
MAX_VOLUME_STEPS = 0x1F + 2 * 0x0F + 2 * 0x0F  # 91
DB_PER_STEP = 1.5


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
    noise_frequency: int = 0  # 5-bit, channels 4 and 5 only
    writes: int = 0

    def frequency_hz(self, clock: int) -> float:
        """Wave repeat rate. The chip plays 32 samples per period."""
        divider = self.frequency or 4096
        return clock / (WAVE_LENGTH * divider)

    def levels_db(self, master_left: int, master_right: int):
        """Attenuation per side in dB, or None when the channel is off."""
        if not self.enabled:
            return None
        left = self.amplitude + 2 * self.balance_left + 2 * master_left
        right = self.amplitude + 2 * self.balance_right + 2 * master_right
        # The + 0.0 turns -0.0 into 0.0 at full volume.
        return (
            -DB_PER_STEP * (MAX_VOLUME_STEPS - max(0, left)) + 0.0,
            -DB_PER_STEP * (MAX_VOLUME_STEPS - max(0, right)) + 0.0,
        )


class HuC6280State:
    """Applies register writes and holds the resulting chip state."""

    def __init__(self, clock: int = DEFAULT_CLOCK) -> None:
        self.clock = clock or DEFAULT_CLOCK
        self.channels = [Channel() for _ in range(NUM_CHANNELS)]
        self.selected = 0
        self.master_left = 0
        self.master_right = 0
        self.lfo_frequency = 0
        self.lfo_control = 0
        self.writes = 0

    def write(self, register: int, value: int) -> None:
        register &= 0x0F
        value &= 0xFF
        self.writes += 1

        if register == 0x00:
            self.selected = value & 0x07
            return
        if register == 0x01:
            self.master_left = (value >> 4) & 0x0F
            self.master_right = value & 0x0F
            return
        if register == 0x08:
            self.lfo_frequency = value
            return
        if register == 0x09:
            self.lfo_control = value
            return
        if self.selected >= NUM_CHANNELS:
            return  # channel 6 and 7 do not exist

        channel = self.channels[self.selected]
        channel.writes += 1
        if register == 0x02:
            channel.frequency = (channel.frequency & 0x0F00) | value
        elif register == 0x03:
            channel.frequency = (channel.frequency & 0x00FF) | ((value & 0x0F) << 8)
        elif register == 0x04:
            channel.enabled = bool(value & 0x80)
            channel.dda = bool(value & 0x40)
            channel.amplitude = value & 0x1F
            if not channel.enabled:
                channel.wave_index = 0  # the chip resets the wave pointer
        elif register == 0x05:
            channel.balance_left = (value >> 4) & 0x0F
            channel.balance_right = value & 0x0F
        elif register == 0x06:
            if channel.dda:
                channel.dda_sample = value & 0x1F
            else:
                channel.waveform[channel.wave_index] = value & 0x1F
                channel.wave_index = (channel.wave_index + 1) % WAVE_LENGTH
        elif register == 0x07:
            if self.selected >= 4:  # noise exists on channels 4 and 5 only
                channel.noise_enabled = bool(value & 0x80)
                channel.noise_frequency = value & 0x1F


def describe_write(register: int, value: int, selected: int) -> str:
    """Text for one register write, given the channel selected before it."""
    register &= 0x0F
    value &= 0xFF
    if register == 0x00:
        return f"select channel {value & 0x07}"
    if register == 0x01:
        return f"master balance L={value >> 4:X} R={value & 0x0F:X}"
    if register == 0x08:
        return f"LFO freq {value}"
    if register == 0x09:
        state = "off" if value & 0x80 else "on"
        return f"LFO {state} mode={value & 0x03}"

    prefix = f"ch{selected}"
    if register == 0x02:
        return f"{prefix} freq low 0x{value:02X}"
    if register == 0x03:
        return f"{prefix} freq high 0x{value & 0x0F:X}"
    if register == 0x04:
        state = "on " if value & 0x80 else "off"
        dda = " dda" if value & 0x40 else ""
        return f"{prefix} {state} amp={value & 0x1F}{dda}"
    if register == 0x05:
        return f"{prefix} balance L={value >> 4:X} R={value & 0x0F:X}"
    if register == 0x06:
        return f"{prefix} wave/dda {value & 0x1F}"
    if register == 0x07:
        state = "on" if value & 0x80 else "off"
        return f"{prefix} noise {state} freq={value & 0x1F}"
    return f"{prefix} reg 0x{register:02X} = 0x{value:02X}"


def note_text(hz: float) -> str:
    """Nearest note plus cents, for example 'A4 +03'."""
    if hz <= 0 or hz > 20000:
        return "--"
    midi = 69 + 12 * math.log2(hz / 440.0)
    nearest = int(round(midi))
    if not 0 <= nearest < 128:
        return "--"
    cents = int(round((midi - nearest) * 100))
    return f"{NOTE_NAMES[nearest % 12]}{nearest // 12 - 1}{cents:+03d}"
