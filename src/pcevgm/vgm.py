"""Reader for VGM (Video Game Music) files.

Covers plain and gzip-compressed input, the fixed header, GD3 tags, and the
command stream. Field offsets follow the VGM_HEADER struct in vgmplay.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field
from typing import Optional

MAGIC = b"Vgm "
SAMPLE_RATE = 44100

# Chip clock fields, name -> header offset. Only the chips the UI names.
# Offsets come from the VGM_HEADER struct in vgmplay (VGMPlay/VGMFile.h).
CLOCK_FIELDS = {
    "SN76489": 0x0C,
    "YM2413": 0x10,
    "YM2612": 0x2C,
    "YM2151": 0x30,
    "SegaPCM": 0x38,
    "HuC6280": 0xA4,
}


class VgmError(Exception):
    """The file is not a VGM file, or its structure is broken."""


@dataclass(frozen=True)
class Gd3:
    """GD3 metadata tag. Empty strings mean the tag field is absent."""

    track: str = ""
    track_jp: str = ""
    game: str = ""
    game_jp: str = ""
    system: str = ""
    system_jp: str = ""
    author: str = ""
    author_jp: str = ""
    date: str = ""
    converter: str = ""
    notes: str = ""


@dataclass(frozen=True)
class VgmHeader:
    version: int
    eof_offset: int  # absolute
    gd3_offset: int  # absolute, 0 if absent
    total_samples: int
    loop_offset: int  # absolute, 0 if absent
    loop_samples: int
    rate: int
    data_offset: int  # absolute
    clocks: dict = field(default_factory=dict)

    @property
    def version_text(self) -> str:
        return f"{self.version >> 8:x}.{self.version & 0xFF:02x}"

    @property
    def huc6280_clock(self) -> int:
        return self.clocks.get("HuC6280", 0)


@dataclass(frozen=True)
class Command:
    """One decoded command from the VGM stream."""

    offset: int  # byte offset in the decompressed file
    sample: int  # absolute sample time when the command runs
    opcode: int
    operands: bytes
    wait: int  # samples this command waits after it runs


@dataclass
class VgmFile:
    path: str
    header: VgmHeader
    gd3: Optional[Gd3]
    commands: list
    warnings: list
    loop_index: Optional[int]  # index into commands, or None


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        return 0
    return struct.unpack_from("<I", data, offset)[0]


def parse_header(data: bytes) -> VgmHeader:
    if len(data) < 0x40 or data[0:4] != MAGIC:
        raise VgmError("not a VGM file: missing 'Vgm ' signature")
    version = _u32(data, 0x08)
    relative_data = _u32(data, 0x34)
    if version >= 0x150 and relative_data:
        data_offset = 0x34 + relative_data
    else:
        data_offset = 0x40

    eof = _u32(data, 0x04)
    gd3 = _u32(data, 0x14)
    loop = _u32(data, 0x1C)

    clocks = {}
    for name, offset in CLOCK_FIELDS.items():
        # A clock field only exists if it sits inside the header.
        if offset + 4 > data_offset:
            continue
        value = _u32(data, offset)
        if value:
            clocks[name] = value & 0x3FFFFFFF  # top bits are dual-chip flags

    return VgmHeader(
        version=version,
        eof_offset=0x04 + eof if eof else len(data),
        gd3_offset=0x14 + gd3 if gd3 else 0,
        total_samples=_u32(data, 0x18),
        loop_offset=0x1C + loop if loop else 0,
        loop_samples=_u32(data, 0x20),
        rate=_u32(data, 0x24),
        data_offset=data_offset,
        clocks=clocks,
    )


def parse_gd3(data: bytes, offset: int) -> Optional[Gd3]:
    if offset <= 0 or offset + 12 > len(data) or data[offset : offset + 4] != b"Gd3 ":
        return None
    length = _u32(data, offset + 8)
    body = data[offset + 12 : offset + 12 + length]
    parts = body.decode("utf-16-le", errors="replace").split("\x00")
    parts += [""] * (11 - len(parts))
    return Gd3(*parts[:11])


def _operand_length(opcode: int, version: int) -> Optional[int]:
    """Bytes that follow the opcode, or None if the opcode is unknown."""
    if opcode in (0x62, 0x63, 0x66):
        return 0
    if opcode == 0x61:
        return 2
    if opcode == 0x68:
        return 11
    if opcode in (0x90, 0x91, 0x95):
        return 4
    if opcode == 0x92:
        return 5
    if opcode == 0x93:
        return 10
    if opcode == 0x94:
        return 1
    if 0x30 <= opcode <= 0x3F:
        return 1
    if 0x40 <= opcode <= 0x4E:
        return 2 if version >= 0x160 else 1
    if opcode in (0x4F, 0x50):
        return 1
    if 0x51 <= opcode <= 0x5F:
        return 2
    if 0x70 <= opcode <= 0x8F:
        return 0
    if 0xA0 <= opcode <= 0xBF:
        return 2
    if 0xC0 <= opcode <= 0xDF:
        return 3
    if 0xE0 <= opcode <= 0xFF:
        return 4
    return None


def _wait_samples(opcode: int, operands: bytes) -> int:
    if opcode == 0x61:
        return struct.unpack_from("<H", operands)[0] if len(operands) == 2 else 0
    if opcode == 0x62:
        return 735  # 1/60 s
    if opcode == 0x63:
        return 882  # 1/50 s
    if 0x70 <= opcode <= 0x7F:
        return (opcode & 0x0F) + 1
    if 0x80 <= opcode <= 0x8F:
        return opcode & 0x0F  # YM2612 DAC write, then wait
    return 0


def parse_commands(data: bytes, start: int, version: int, stop: int):
    """Decode the command stream. Returns (commands, warnings)."""
    commands = []
    warnings = []
    end = min(stop if stop else len(data), len(data))
    position = start
    sample = 0

    while position < end:
        opcode = data[position]
        command_start = position
        position += 1

        if opcode == 0x67:  # data block: 0x67 0x66 tt ssssssss <payload>
            if position + 6 > end:
                warnings.append(f"truncated data block at 0x{command_start:X}")
                break
            size = _u32(data, position + 2) & 0x7FFFFFFF
            operands = data[position : position + 6 + size]
            position += 6 + size
            commands.append(Command(command_start, sample, opcode, operands, 0))
            continue

        length = _operand_length(opcode, version)
        if length is None:
            warnings.append(
                f"unknown command 0x{opcode:02X} at 0x{command_start:X}; stopped parsing"
            )
            break
        if position + length > end:
            warnings.append(f"truncated command 0x{opcode:02X} at 0x{command_start:X}")
            break

        operands = data[position : position + length]
        position += length
        wait = _wait_samples(opcode, operands)
        commands.append(Command(command_start, sample, opcode, operands, wait))
        sample += wait
        if opcode == 0x66:
            break

    return commands, warnings


def describe_command(command: Command) -> str:
    """Short text for any command the HuC6280 decoder does not own."""
    opcode = command.opcode
    if opcode == 0x61:
        return f"wait {command.wait}"
    if opcode == 0x62:
        return "wait 735 (1/60 s)"
    if opcode == 0x63:
        return "wait 882 (1/50 s)"
    if 0x70 <= opcode <= 0x7F:
        return f"wait {command.wait}"
    if 0x80 <= opcode <= 0x8F:
        return f"YM2612 DAC write, wait {command.wait}"
    if opcode == 0x66:
        return "end of sound data"
    if opcode == 0x67:
        return f"data block, {max(0, len(command.operands) - 6)} bytes"
    if opcode == 0x68:
        return "PCM RAM write"
    if opcode == 0xE0:
        return "seek PCM data bank"
    return f"opcode 0x{opcode:02X}"


def load(path: str) -> VgmFile:
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    header = parse_header(raw)
    gd3 = parse_gd3(raw, header.gd3_offset)
    stop = header.gd3_offset or header.eof_offset or len(raw)
    commands, warnings = parse_commands(raw, header.data_offset, header.version, stop)

    loop_index = None
    if header.loop_offset:
        for index, command in enumerate(commands):
            if command.offset == header.loop_offset:
                loop_index = index
                break
        if loop_index is None:
            warnings.append(
                f"loop offset 0x{header.loop_offset:X} does not land on a command"
            )

    return VgmFile(path, header, gd3, commands, warnings, loop_index)
