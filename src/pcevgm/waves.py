"""Extraction of the wave tables a track uploads to the HuC6280.

A wave table is 32 samples of 5 bits. The chip receives one sample per write
to the wave data register. This module watches the stream for a complete pass
of 32 samples and keeps the table as it stood at that moment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .huc6280 import (
    DEFAULT_CLOCK,
    NUM_CHANNELS,
    REG_WAVE_DATA,
    REGISTER_MASK,
    WAVE_LENGTH,
    HuC6280State,
    sparkline,
)
from .player import HUC6280_WRITE
from .vgm import SAMPLE_RATE, VgmFile

PCM_SUFFIX = ".pcm"
HEX_SUFFIX = ".hex"
MANIFEST_NAME = "manifest.txt"
FOLDER_SUFFIX = ".wavs"
HEX_PER_LINE = 16


@dataclass
class Wave:
    """One distinct wave table, plus every place the track uploads it."""

    samples: bytes  # WAVE_LENGTH bytes, each 0 to 31
    uploads: list = field(default_factory=list)  # (command index, sample time, channel)

    @property
    def channels(self):
        return sorted({channel for _, _, channel in self.uploads})

    @property
    def first_upload(self):
        return self.uploads[0]


def extract(vgm: VgmFile) -> list:
    """Every distinct wave table the track uploads, in first-use order."""
    state = HuC6280State(vgm.header.huc6280_clock or DEFAULT_CLOCK)
    waves = []
    by_samples = {}

    for index, command in enumerate(vgm.commands):
        if command.opcode != HUC6280_WRITE or len(command.operands) != 2:
            continue
        register, value = command.operands
        channel_index = state.selected  # a wave write never changes the selection
        state.write(register, value)

        if register & REGISTER_MASK != REG_WAVE_DATA:
            continue
        if channel_index >= NUM_CHANNELS:
            continue
        channel = state.channels[channel_index]
        if channel.dda or channel.wave_index != 0:
            continue  # DDA output, or the pass is not finished yet

        samples = bytes(channel.waveform)
        wave = by_samples.get(samples)
        if wave is None:
            wave = Wave(samples)
            by_samples[samples] = wave
            waves.append(wave)
        wave.uploads.append((index, command.sample, channel_index))

    return waves


def hex_text(samples: bytes) -> str:
    """The same bytes as two-digit hex, HEX_PER_LINE to a line."""
    lines = [
        " ".join(f"{value:02X}" for value in samples[start : start + HEX_PER_LINE])
        for start in range(0, len(samples), HEX_PER_LINE)
    ]
    return "\n".join(lines) + "\n"


def folder_for(path: str) -> str:
    return path + FOLDER_SUFFIX


def _time_text(sample: int) -> str:
    seconds = sample / SAMPLE_RATE
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def _manifest(vgm: VgmFile, waves: list, names: list) -> str:
    uploads = sum(len(wave.uploads) for wave in waves)
    lines = [
        f"source   {vgm.path}",
        f"waves    {len(waves)} distinct, {uploads} uploads",
        f"format   {WAVE_LENGTH} bytes per .pcm file, one byte per sample, values 0 to 31",
        "         each .hex file holds the same bytes as text",
        "",
    ]
    for wave, name in zip(waves, names):
        index, sample, channel = wave.first_upload
        values = [f"{value:2d}" for value in wave.samples]
        half = WAVE_LENGTH // 2
        lines += [
            name,
            f"  uploads   {len(wave.uploads)}",
            f"  channels  {', '.join(str(c) for c in wave.channels)}",
            f"  first     {_time_text(sample)} at command {index} on channel {channel}",
            f"  samples   {' '.join(values[:half])}",
            f"            {' '.join(values[half:])}",
            f"  plot      {sparkline(wave.samples)}",
            "",
        ]
    return "\n".join(lines)


def write_files(vgm: VgmFile, waves: list, out_dir: str) -> dict:
    """Write a .pcm and a .hex file per wave, plus a manifest.

    Returns a short report for the caller to print.
    """
    os.makedirs(out_dir, exist_ok=True)
    width = max(2, len(str(max(0, len(waves) - 1))))
    stems = [f"wave-{index:0{width}d}" for index in range(len(waves))]

    for wave, stem in zip(waves, stems):
        with open(os.path.join(out_dir, stem + PCM_SUFFIX), "wb") as handle:
            handle.write(wave.samples)
        with open(os.path.join(out_dir, stem + HEX_SUFFIX), "w", encoding="utf-8") as handle:
            handle.write(hex_text(wave.samples))

    names = [stem + PCM_SUFFIX for stem in stems]
    with open(os.path.join(out_dir, MANIFEST_NAME), "w", encoding="utf-8") as handle:
        handle.write(_manifest(vgm, waves, names))

    keep = {stem + suffix for stem in stems for suffix in (PCM_SUFFIX, HEX_SUFFIX)}
    keep.add(MANIFEST_NAME)
    stale = sorted(
        name
        for name in os.listdir(out_dir)
        if name.endswith((PCM_SUFFIX, HEX_SUFFIX)) and name not in keep
    )
    return {"directory": out_dir, "stems": stems, "written": names, "stale": stale}
