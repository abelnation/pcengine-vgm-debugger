import gzip
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pcevgm import ableton, waves
from pcevgm.huc6280 import WAVE_LENGTH
from pcevgm.notes import FRAME, analyse
from tests.test_notes import C4_DIVIDER, FLAT, G4_DIVIDER, make_vgm, ordered, play, setup


def build_analysis(commands):
    vgm = make_vgm(commands)
    return vgm, analyse(vgm)


def two_instruments():
    return ordered(
        setup(0), setup(1, wave=FLAT),
        play(0, 0, C4_DIVIDER, [31, 28, 0]),          # falls to silence
        play(1, 0, G4_DIVIDER, [31, 28, 24, 24, 24]),  # settles and holds
    )


class AdsrTests(unittest.TestCase):
    def setUp(self):
        self.vgm, self.analysis = build_analysis(two_instruments())

    def test_a_struck_note_attacks_instantly(self):
        attack, _, _, _ = ableton.adsr(self.analysis, 0)
        self.assertEqual(attack, ableton.ATTACK_RANGE[0])

    def test_an_envelope_reaching_zero_sustains_at_the_floor(self):
        _, _, sustain, release = ableton.adsr(self.analysis, 0)
        self.assertEqual(sustain, ableton.LEVEL_FLOOR)
        self.assertLess(release, 2 * FRAME / 44100 * 1000)  # the chip cut it

    def test_a_held_envelope_sustains_where_it_settles(self):
        _, _, sustain, release = ableton.adsr(self.analysis, 1)
        # 31 down to 24 is seven steps of 1.5 dB, so -10.5 dB.
        self.assertAlmostEqual(sustain, 10 ** (-10.5 / 20), places=4)
        self.assertGreater(release, 100)

    def test_every_value_lands_inside_simpler_limits(self):
        for instrument in range(len(self.analysis.instruments)):
            attack, decay, sustain, release = ableton.adsr(self.analysis, instrument)
            self.assertGreaterEqual(attack, ableton.ATTACK_RANGE[0])
            self.assertLessEqual(attack, ableton.ATTACK_RANGE[1])
            for time in (decay, release):
                self.assertGreaterEqual(time, ableton.TIME_RANGE[0])
                self.assertLessEqual(time, ableton.TIME_RANGE[1])
            self.assertGreaterEqual(sustain, ableton.LEVEL_FLOOR)
            self.assertLessEqual(sustain, 1.0)


class SamplePathTests(unittest.TestCase):
    WAV = os.path.join("proj", "song.vgz.wavs", "wave-01.wav")
    LIBRARY = os.path.join("home", "Music", "Ableton", "User Library")

    def test_no_sample_dir_means_no_relative_path(self):
        relative, absolute = ableton.sample_paths(self.WAV)
        self.assertEqual(relative, "")
        self.assertEqual(absolute, os.path.abspath(self.WAV))

    def test_only_the_wave_file_name_is_appended(self):
        relative, _ = ableton.sample_paths(self.WAV, sample_dir="Samples/PCE")
        self.assertEqual(relative, "Samples/PCE/wave-01.wav")
        self.assertNotIn("song.vgz.wavs", relative)
        self.assertNotIn("proj", relative)

    def test_the_library_gives_the_absolute_path(self):
        relative, absolute = ableton.sample_paths(
            self.WAV, library=self.LIBRARY, sample_dir="Samples/PCE"
        )
        self.assertEqual(
            absolute,
            os.path.join(os.path.abspath(self.LIBRARY), "Samples", "PCE", "wave-01.wav"),
        )
        self.assertTrue(absolute.endswith(os.path.join("PCE", "wave-01.wav")))
        self.assertEqual(relative, "Samples/PCE/wave-01.wav")

    def test_a_library_without_a_sample_dir_keeps_the_real_path(self):
        _, absolute = ableton.sample_paths(self.WAV, library=self.LIBRARY)
        self.assertEqual(absolute, os.path.abspath(self.WAV))

    def test_stray_slashes_are_trimmed(self):
        for given in ("/Samples/PCE/", "Samples/PCE", "\\Samples\\PCE"):
            relative, _ = ableton.sample_paths(self.WAV, sample_dir=given)
            self.assertEqual(relative, "Samples/PCE/wave-01.wav", given)

    def test_the_relative_path_never_climbs_out_of_the_library(self):
        relative, _ = ableton.sample_paths(
            self.WAV, library=self.LIBRARY, sample_dir="Samples/PCE"
        )
        self.assertNotIn("..", relative)


class TemplateTests(unittest.TestCase):
    def test_the_template_ships_with_the_package(self):
        self.assertTrue(os.path.exists(ableton.TEMPLATE))

    def test_the_template_names_no_machine(self):
        with open(ableton.TEMPLATE, "rb") as handle:
            text = gzip.decompress(handle.read()).decode()
        self.assertNotIn("/Users/", text)


class BuildTests(unittest.TestCase):
    def preset(self, values=(0.1, 200.0, 0.5, 300.0)):
        with tempfile.TemporaryDirectory() as out:
            wav = os.path.join(out, "wave-00.wav")
            waves.write_wav(wav, [0] * WAVE_LENGTH, waves.cycle_rate())
            blob = ableton.build("inst-00", wav, values, "Samples/wave-00.wav")
        return ET.fromstring(gzip.decompress(blob))

    def test_the_preset_is_gzipped_xml_live_would_recognise(self):
        root = self.preset()
        self.assertEqual(root.tag, "Ableton")
        self.assertIsNotNone(root.find("OriginalSimpler"))

    def test_the_sample_is_a_looping_single_cycle_at_c4(self):
        part = self.preset().find(ableton.PART)
        self.assertEqual(part.find("RootKey").get("Value"), str(ableton.ROOT_KEY))
        self.assertEqual(part.find("SampleEnd").get("Value"), str(WAVE_LENGTH - 1))
        for loop in ("SustainLoop", "ReleaseLoop"):
            self.assertEqual(part.find(f"{loop}/Mode").get("Value"), str(ableton.SUSTAIN_LOOP))
            self.assertEqual(part.find(f"{loop}/End").get("Value"), str(WAVE_LENGTH - 1))
        self.assertEqual(
            part.find("SampleRef/DefaultSampleRate").get("Value"), str(waves.cycle_rate())
        )

    def test_the_crc_is_zeroed(self):
        part = self.preset().find(ableton.PART)
        self.assertEqual(part.find("SampleRef/FileRef/OriginalCrc").get("Value"), "0")

    def test_the_envelope_carries_the_values_given(self):
        envelope = self.preset((0.1, 200.0, 0.5, 300.0)).find(ableton.ENVELOPE)
        self.assertEqual(float(envelope.find("DecayTime/Manual").get("Value")), 200.0)
        self.assertEqual(float(envelope.find("SustainLevel/Manual").get("Value")), 0.5)
        self.assertEqual(float(envelope.find("ReleaseTime/Manual").get("Value")), 300.0)

    def test_a_missing_field_is_reported(self):
        with self.assertRaises(KeyError):
            ableton._set(self.preset(), "NoSuchField", 1)


class WriteTests(unittest.TestCase):
    def test_one_preset_per_instrument_that_has_a_wave(self):
        vgm, analysis = build_analysis(two_instruments())
        with tempfile.TemporaryDirectory() as root:
            wave_dir = os.path.join(root, "wavs")
            found = waves.extract(vgm)
            waves.write_files(vgm, found, wave_dir)
            out = os.path.join(root, "ableton")
            written = ableton.write_presets(vgm, analysis, wave_dir, out)
            self.assertEqual(len(written), len(analysis.instruments))
            for name, _, _, _ in written:
                self.assertTrue(os.path.exists(os.path.join(out, name + ableton.SUFFIX)))

    def test_an_instrument_with_no_extracted_wave_is_skipped(self):
        vgm, analysis = build_analysis(two_instruments())
        with tempfile.TemporaryDirectory() as root:
            empty = os.path.join(root, "wavs")
            os.makedirs(empty)
            out = os.path.join(root, "ableton")
            self.assertEqual(ableton.write_presets(vgm, analysis, empty, out), [])

    def test_the_sample_dir_reaches_the_written_preset(self):
        vgm, analysis = build_analysis(two_instruments())
        with tempfile.TemporaryDirectory() as root:
            wave_dir = os.path.join(root, "wavs")
            waves.write_files(vgm, waves.extract(vgm), wave_dir)
            out = os.path.join(root, "ableton")
            library = os.path.join(root, "library")
            ableton.write_presets(
                vgm, analysis, wave_dir, out, library=library, sample_dir="Samples/PCE"
            )
            with open(os.path.join(out, "inst-00" + ableton.SUFFIX), "rb") as handle:
                blob = handle.read()
            part = ET.fromstring(gzip.decompress(blob)).find(ableton.PART)
            relative = part.find("SampleRef/FileRef/RelativePath").get("Value")
            absolute = part.find("SampleRef/FileRef/Path").get("Value")
        self.assertEqual(relative, "Samples/PCE/wave-00.wav")
        self.assertTrue(absolute.startswith(os.path.abspath(library)))
        self.assertTrue(absolute.endswith(os.path.join("Samples", "PCE", "wave-00.wav")))


if __name__ == "__main__":
    unittest.main()
