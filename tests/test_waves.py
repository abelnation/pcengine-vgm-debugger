import math
import os
import struct
import tempfile
import unittest
import wave as wave_file

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


class AudioTests(unittest.TestCase):
    def test_pcm16_centres_the_five_bit_range(self):
        low, high = waves.to_pcm16(bytes([0, 31]))
        self.assertEqual(low, -waves.WAV_PEAK)
        self.assertEqual(high, waves.WAV_PEAK)

    def test_preview_length_is_a_whole_number_of_cycles(self):
        frames = waves.preview_frames(RAMP, hz=441.0, seconds=1.0)
        self.assertEqual(len(frames), waves.WAV_RATE)
        self.assertEqual(len(frames) / (waves.WAV_RATE / 441.0), 441)

    def test_preview_holds_the_asked_pitch(self):
        square = bytes([31] * 16 + [0] * 16)
        frames = waves.preview_frames(square, hz=440.0, seconds=1.0)
        rising = sum(1 for i in range(1, len(frames)) if frames[i - 1] < 0 <= frames[i])
        self.assertEqual(rising, 440)

    def test_preview_fades_both_ends(self):
        frames = waves.preview_frames(RAMP)
        self.assertEqual(frames[0], 0)
        self.assertEqual(frames[-1], 0)
        self.assertEqual(max(abs(value) for value in frames), waves.WAV_PEAK)


class NameTests(unittest.TestCase):
    def test_stems_pad_to_at_least_two_digits(self):
        self.assertEqual(waves.stem_names(3), ["wave-00", "wave-01", "wave-02"])

    def test_stems_widen_past_a_hundred_waves(self):
        stems = waves.stem_names(101)
        self.assertEqual(stems[0], "wave-000")
        self.assertEqual(stems[-1], "wave-100")

    def test_names_map_each_table_to_its_stem(self):
        vgm = make_vgm(upload(0, RAMP) + upload(1, FLAT))
        names = waves.names_by_samples(waves.extract(vgm))
        self.assertEqual(names[RAMP], "wave-00")
        self.assertEqual(names[FLAT], "wave-01")

    def test_an_unknown_table_has_no_name(self):
        names = waves.names_by_samples(waves.extract(make_vgm(upload(0, RAMP))))
        self.assertIsNone(names.get(FLAT))

    def test_names_match_the_files_the_dump_writes(self):
        vgm = make_vgm(upload(0, RAMP) + upload(1, FLAT))
        found = waves.extract(vgm)
        names = waves.names_by_samples(found)
        with tempfile.TemporaryDirectory() as out:
            report = waves.write_files(vgm, found, out)
            self.assertEqual(sorted(names.values()), sorted(report["stems"]))
            for samples, stem in names.items():
                with open(os.path.join(out, stem + waves.PCM_SUFFIX), "rb") as handle:
                    self.assertEqual(handle.read(), samples)


class WriteTests(unittest.TestCase):
    def test_folder_name_appends_to_the_input_path(self):
        self.assertEqual(waves.folder_for("a/b.vgz"), "a/b.vgz.wavs")

    def test_files_hold_the_raw_samples(self):
        vgm = make_vgm(upload(0, RAMP) + upload(1, FLAT))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            report = waves.write_files(vgm, found, out)
            self.assertEqual(report["written"], ["wave-00.pcm", "wave-01.pcm"])
            for stem in report["stems"]:
                for suffix in waves.SUFFIXES:
                    self.assertTrue(os.path.exists(os.path.join(out, stem + suffix)), stem + suffix)
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

    def test_wav_holds_one_cycle_of_the_same_samples(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out)
            with wave_file.open(os.path.join(out, "wave-00" + waves.WAV_SUFFIX)) as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), waves.WAV_WIDTH)
                self.assertEqual(handle.getframerate(), waves.cycle_rate())
                self.assertEqual(handle.getnframes(), WAVE_LENGTH)
                frames = handle.readframes(WAVE_LENGTH)
        values = struct.unpack(f"<{WAVE_LENGTH}h", frames)
        self.assertEqual(list(values), waves.to_pcm16(RAMP))

    def test_the_cycle_rate_puts_the_wave_at_c4(self):
        rate = waves.cycle_rate()
        self.assertEqual(rate, 8372)
        played = rate / WAVE_LENGTH
        cents = 1200 * math.log2(played / waves.CYCLE_HZ)
        self.assertLess(abs(cents), 1.0)

    def test_the_cycle_rate_follows_the_asked_pitch(self):
        self.assertEqual(waves.cycle_rate(waves.CYCLE_HZ * 2), 2 * 8372)
        self.assertEqual(waves.cycle_rate(440.0), round(WAVE_LENGTH * 440.0))
        self.assertGreaterEqual(waves.cycle_rate(0.0), 1)

    def test_the_pitch_is_the_only_thing_the_rate_changes(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out, cycle_hz=440.0)
            path = os.path.join(out, "wave-00" + waves.WAV_SUFFIX)
            with wave_file.open(path) as handle:
                self.assertEqual(handle.getframerate(), waves.cycle_rate(440.0))
                frames = handle.readframes(WAVE_LENGTH)
        self.assertEqual(
            list(struct.unpack(f"<{WAVE_LENGTH}h", frames)), waves.to_pcm16(RAMP)
        )

    def test_the_long_preview_keeps_the_standard_rate(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out)
            with wave_file.open(os.path.join(out, "wave-00" + waves.LONG_WAV_SUFFIX)) as h:
                self.assertEqual(h.getframerate(), waves.WAV_RATE)

    def test_long_wav_runs_for_the_asked_length(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            waves.write_files(vgm, found, out, preview_hz=100.0, preview_seconds=0.5)
            with wave_file.open(os.path.join(out, "wave-00" + waves.LONG_WAV_SUFFIX)) as handle:
                self.assertEqual(handle.getnframes(), waves.WAV_RATE // 2)

    def test_leftover_files_from_an_earlier_run_are_reported(self):
        vgm = make_vgm(upload(0, RAMP))
        found = waves.extract(vgm)
        with tempfile.TemporaryDirectory() as out:
            with open(os.path.join(out, "wave-09.pcm"), "wb") as handle:
                handle.write(b"old")
            with open(os.path.join(out, "wave-09.hex"), "w") as handle:
                handle.write("00")
            with open(os.path.join(out, "wave-09.long.wav"), "wb") as handle:
                handle.write(b"old")
            report = waves.write_files(vgm, found, out)
            self.assertEqual(
                report["stale"], ["wave-09.hex", "wave-09.long.wav", "wave-09.pcm"]
            )
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
