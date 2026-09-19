#!/usr/bin/env python3
"""Write examples/scale.vgm: a short HuC6280 test tone sequence.

The file lets you run the debugger without a real game rip.
"""

from __future__ import annotations

import math
import os
import struct

CLOCK = 3_579_545
HEADER_SIZE = 0x100
FRAME = 735  # samples in one NTSC video frame

SCALE_HZ = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25]


def sine_wave():
    return [int(round(15.5 + 15.5 * math.sin(2 * math.pi * i / 32))) & 0x1F for i in range(32)]


def square_wave():
    return [31 if i < 16 else 0 for i in range(32)]


def divider(hz):
    return max(1, min(0xFFF, round(CLOCK / (32 * hz))))


class Stream:
    def __init__(self):
        self.data = bytearray()
        self.samples = 0

    def write(self, register, value):
        self.data += bytes((0xB9, register, value & 0xFF))

    def wait_frames(self, count):
        self.data += bytes((0x62,)) * count
        self.samples += FRAME * count

    def select(self, channel):
        self.write(0x00, channel)

    def load_wave(self, channel, wave):
        self.select(channel)
        self.write(0x04, 0x00)  # off, amp 0: this resets the wave pointer
        for sample in wave:
            self.write(0x06, sample)

    def note(self, channel, hz, amplitude):
        value = divider(hz)
        self.select(channel)
        self.write(0x02, value & 0xFF)
        self.write(0x03, (value >> 8) & 0x0F)
        self.write(0x04, 0x80 | (amplitude & 0x1F))

    def note_off(self, channel):
        self.select(channel)
        self.write(0x04, 0x00)

    def end(self):
        self.data += bytes((0x66,))


def build_stream():
    stream = Stream()
    stream.write(0x01, 0xFF)  # master balance, both sides full
    for channel in range(3):
        stream.load_wave(channel, sine_wave() if channel < 2 else square_wave())
        stream.select(channel)
        stream.write(0x05, 0xFF)  # channel balance, both sides full

    loop_offset = len(stream.data)
    for step, hz in enumerate(SCALE_HZ):
        stream.note(0, hz, 0x1F)
        stream.note(1, hz * 2, 0x14)
        if step % 2 == 0:
            stream.note(2, hz / 2, 0x18)
        stream.wait_frames(20)
        stream.note_off(0)
        stream.note_off(1)
        stream.wait_frames(4)

    stream.note_off(2)
    stream.wait_frames(10)
    stream.end()
    return stream, loop_offset


def gd3_block():
    fields = [
        "Scale Test", "", "pcevgm examples", "", "NEC PC Engine", "",
        "pcevgm", "", "2026", "tools/make_example.py", "Synthetic test file.",
    ]
    body = b"".join(text.encode("utf-16-le") + b"\x00\x00" for text in fields)
    return b"Gd3 " + struct.pack("<II", 0x0100, len(body)) + body


def main():
    stream, loop_offset = build_stream()
    gd3 = gd3_block()
    data_start = HEADER_SIZE
    gd3_start = data_start + len(stream.data)
    total = gd3_start + len(gd3)

    header = bytearray(HEADER_SIZE)
    header[0x00:0x04] = b"Vgm "
    struct.pack_into("<I", header, 0x04, total - 0x04)  # EOF offset
    struct.pack_into("<I", header, 0x08, 0x161)  # version 1.61
    struct.pack_into("<I", header, 0x14, gd3_start - 0x14)
    struct.pack_into("<I", header, 0x18, stream.samples)
    struct.pack_into("<I", header, 0x1C, data_start + loop_offset - 0x1C)
    struct.pack_into("<I", header, 0x20, stream.samples)
    struct.pack_into("<I", header, 0x24, 60)  # frame rate hint
    struct.pack_into("<I", header, 0x34, HEADER_SIZE - 0x34)
    struct.pack_into("<I", header, 0xA4, CLOCK)  # HuC6280 clock

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "examples", "scale.vgm")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(bytes(header) + bytes(stream.data) + gd3)
    print(f"wrote {path} ({total} bytes, {stream.samples} samples)")


if __name__ == "__main__":
    main()
