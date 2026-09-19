import struct
import unittest

from pcevgm import vgm


def make_file(stream: bytes, total_samples: int = 0, loop_offset: int = 0) -> bytes:
    """Build a minimal version 1.61 VGM file around a command stream."""
    header = bytearray(0x100)
    header[0x00:0x04] = b"Vgm "
    struct.pack_into("<I", header, 0x04, 0x100 + len(stream) - 0x04)
    struct.pack_into("<I", header, 0x08, 0x161)
    struct.pack_into("<I", header, 0x18, total_samples)
    if loop_offset:
        struct.pack_into("<I", header, 0x1C, loop_offset - 0x1C)
    struct.pack_into("<I", header, 0x34, 0x100 - 0x34)
    struct.pack_into("<I", header, 0xA4, 3_579_545)
    return bytes(header) + stream


class HeaderTests(unittest.TestCase):
    def test_rejects_non_vgm(self):
        with self.assertRaises(vgm.VgmError):
            vgm.parse_header(b"\x00" * 0x80)

    def test_reads_version_clock_and_data_offset(self):
        header = vgm.parse_header(make_file(b"\x66"))
        self.assertEqual(header.version, 0x161)
        self.assertEqual(header.version_text, "1.61")
        self.assertEqual(header.data_offset, 0x100)
        self.assertEqual(header.huc6280_clock, 3_579_545)

    def test_clock_field_outside_header_is_ignored(self):
        data = bytearray(make_file(b"\x66"))
        struct.pack_into("<I", data, 0x34, 0x40 - 0x34)  # data starts at 0x40
        header = vgm.parse_header(bytes(data))
        self.assertEqual(header.data_offset, 0x40)
        self.assertEqual(header.huc6280_clock, 0)


class CommandTests(unittest.TestCase):
    def parse(self, stream: bytes):
        commands, warnings = vgm.parse_commands(stream, 0, 0x161, len(stream))
        return commands, warnings

    def test_waits_accumulate_sample_time(self):
        stream = bytes([0x62, 0x63, 0x61, 0x10, 0x00, 0x71, 0x66])
        commands, warnings = self.parse(stream)
        self.assertEqual(warnings, [])
        self.assertEqual([c.wait for c in commands], [735, 882, 16, 2, 0])
        self.assertEqual([c.sample for c in commands], [0, 735, 1617, 1633, 1635])

    def test_huc6280_write_keeps_register_and_value(self):
        commands, _ = self.parse(bytes([0xB9, 0x04, 0x9F, 0x66]))
        self.assertEqual(commands[0].opcode, 0xB9)
        self.assertEqual(commands[0].operands, b"\x04\x9f")

    def test_stops_at_end_marker(self):
        commands, _ = self.parse(bytes([0x66, 0xB9, 0x00, 0x00]))
        self.assertEqual(len(commands), 1)

    def test_data_block_consumes_payload(self):
        stream = bytes([0x67, 0x66, 0x00]) + struct.pack("<I", 3) + b"abc" + bytes([0x66])
        commands, warnings = self.parse(stream)
        self.assertEqual(warnings, [])
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1].opcode, 0x66)

    def test_unknown_opcode_warns_and_stops(self):
        commands, warnings = self.parse(bytes([0x62, 0x00, 0x66]))
        self.assertEqual(len(commands), 1)
        self.assertIn("unknown command 0x00", warnings[0])


class LoadTests(unittest.TestCase):
    def test_finds_loop_command(self):
        import os
        import tempfile

        stream = bytes([0x62, 0xB9, 0x00, 0x01, 0x66])
        raw = make_file(stream, total_samples=735, loop_offset=0x100 + 1)
        handle, path = tempfile.mkstemp(suffix=".vgm")
        os.close(handle)
        try:
            with open(path, "wb") as out:
                out.write(raw)
            parsed = vgm.load(path)
        finally:
            os.unlink(path)
        self.assertEqual(parsed.loop_index, 1)
        self.assertEqual(parsed.warnings, [])


if __name__ == "__main__":
    unittest.main()
