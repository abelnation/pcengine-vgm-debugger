import os
import tempfile
import unittest

from pcevgm import waves
from pcevgm.huc6280 import (
    CONTROL_DDA,
    CONTROL_ENABLE,
    REG_CHANNEL_SELECT,
    REG_CONTROL,
    REG_WAVE_DATA,
    WAVE_LENGTH,
)
from pcevgm.vgm import Command, Gd3, VgmFile, VgmHeader

RAMP = bytes(range(WAVE_LENGTH))
FLAT = bytes([7] * WAVE_LENGTH)


def write(register, value):
    return Command(0, 0, 0xB9, bytes((register, value)), 0)


def upload(channel, samples, control=None):
    commands = [write(REG_CHANNEL_SELECT, channel)]
    if control is not None:
        commands.append(write(REG_CONTROL, control))
    commands += [write(REG_WAVE_DATA, value) for value in samples]
    return commands


def make_vgm(commands):
    header = VgmHeader(
        version=0x161,
        eof_offset=0,
        gd3_offset=0,
        total_samples=0,
        loop_offset=0,
        loop_samples=0,
        rate=60,
        data_offset=0x100,
        clocks={"HuC6280": 3_579_545},
    )
    return VgmFile("test.vgm", header, Gd3(), commands, [], None)


class ExtractTests(unittest.TestCase):
    def test_full_pass_is_captured(self):
        found = waves.extract(make_vgm(upload(0, RAMP)))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].samples, RAMP)
        self.assertEqual(found[0].channels, [0])

    def test_identical_tables_collapse_to_one_wave(self):
        found = waves.extract(make_vgm(upload(0, RAMP) + upload(3, RAMP)))
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0].uploads), 2)
        self.assertEqual(found[0].channels, [0, 3])

    def test_different_tables_stay_separate(self):
        found = waves.extract(make_vgm(upload(0, RAMP) + upload(1, FLAT)))
        self.assertEqual([wave.samples for wave in found], [RAMP, FLAT])

    def test_partial_pass_is_not_captured(self):
        found = waves.extract(make_vgm(upload(0, RAMP[:-1])))
        self.assertEqual(found, [])

    def test_dda_writes_are_not_wave_data(self):
        commands = upload(0, RAMP, control=CONTROL_ENABLE | CONTROL_DDA)
        self.assertEqual(waves.extract(make_vgm(commands)), [])

    def test_a_second_pass_on_one_channel_is_a_second_upload(self):
        found = waves.extract(make_vgm(upload(0, RAMP) + upload(0, RAMP)))
        self.assertEqual(len(found[0].uploads), 2)

    def test_writes_to_channel_6_are_ignored(self):
        self.assertEqual(waves.extract(make_vgm(upload(6, RAMP))), [])


class WriteTests(unittest.TestCase):
    def test_folder_name_appends_to_the_input_path(self):
        self.assertEqual(waves.folder_for("a/b.vgz"), "a/b.vgz.wavs")

    def test_files_hold_the_raw_samples(self):
        vgm = make_vgm(upload(0, RAMP) + upload(1, FLAT))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            report = waves.write_files(vgm, found, out)
            self.assertEqual(report["written"], ["wave-00.pcm", "wave-01.pcm"])
            with open(os.path.join(out, "wave-00.pcm"), "rb") as handle:
                self.assertEqual(handle.read(), RAMP)
            with open(os.path.join(out, "wave-01.pcm"), "rb") as handle:
                self.assertEqual(handle.read(), FLAT)
            self.assertTrue(os.path.exists(os.path.join(out, waves.MANIFEST_NAME)))
            self.assertEqual(report["stale"], [])

    def test_hex_file_mirrors_the_pcm_bytes(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out)
            with open(os.path.join(out, "wave-00" + waves.HEX_SUFFIX), encoding="utf-8") as handle:
                text = handle.read()
        self.assertEqual(text.split("\n")[0], " ".join(f"{v:02X}" for v in RAMP[:16]))
        self.assertEqual(bytes.fromhex(text.replace("\n", " ")), RAMP)

    def test_hex_text_wraps_at_sixteen_bytes(self):
        lines = waves.hex_text(RAMP).rstrip("\n").split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].split()[0], "10")

    def test_leftover_files_from_an_earlier_run_are_reported(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            with open(os.path.join(out, "wave-09.pcm"), "wb") as handle:
                handle.write(b"old")
            with open(os.path.join(out, "wave-09.hex"), "w") as handle:
                handle.write("00")
            report = waves.write_files(vgm, found, out)
            self.assertEqual(report["stale"], ["wave-09.hex", "wave-09.pcm"])
            self.assertTrue(os.path.exists(os.path.join(out, "wave-09.pcm")))

    def test_manifest_names_every_wave(self):
        vgm = make_vgm(upload(0, RAMP) + upload(1, FLAT))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out)
            with open(os.path.join(out, waves.MANIFEST_NAME), encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("2 distinct, 2 uploads", text)
        self.assertIn("wave-00.pcm", text)
        self.assertIn("wave-01.pcm", text)


if __name__ == "__main__":
    unittest.main()
