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


class GroupingTests(unittest.TestCase):
    def test_identical_settings_are_one_preset(self):
        values = (0.1, 200.0, 0.5, 300.0)
        self.assertTrue(ableton.same_preset(values, values))

    def test_a_time_inside_one_frame_is_the_same_time(self):
        self.assertTrue(ableton.same_preset(
            (0.1, 200.0, 0.5, 300.0), (0.1, 200.0 + ableton.FRAME_MS - 1, 0.5, 300.0)))

    def test_a_long_time_compares_by_fraction(self):
        self.assertTrue(ableton.same_preset(
            (0.1, 1000.0, 0.5, 300.0), (0.1, 1090.0, 0.5, 300.0), tolerance=0.10))
        self.assertFalse(ableton.same_preset(
            (0.1, 1000.0, 0.5, 300.0), (0.1, 1300.0, 0.5, 300.0), tolerance=0.10))

    def test_a_sustain_within_one_amplitude_step_is_the_same_level(self):
        step = 10 ** (-1.4 / 20)
        self.assertTrue(ableton.same_preset(
            (0.1, 200.0, 1.0, 300.0), (0.1, 200.0, step, 300.0)))
        far = 10 ** (-4.0 / 20)
        self.assertFalse(ableton.same_preset(
            (0.1, 200.0, 1.0, 300.0), (0.1, 200.0, far, 300.0)))

    def test_every_field_is_compared(self):
        base = (0.1, 200.0, 0.5, 300.0)
        for index, other in enumerate(((900.0, 200.0, 0.5, 300.0),
                                       (0.1, 4000.0, 0.5, 300.0),
                                       (0.1, 200.0, 0.01, 300.0),
                                       (0.1, 200.0, 0.5, 9000.0))):
            self.assertFalse(ableton.same_preset(base, other), index)


class GroupInstrumentTests(unittest.TestCase):
    def analysis(self):
        return build_analysis(two_instruments())[1]

    def test_a_different_wave_never_shares_a_preset(self):
        a = self.analysis()
        mapping = ableton.group_instruments(a, min_notes=1)
        waves = {}
        for instrument, head in mapping.items():
            waves.setdefault(head, set()).add(a.instruments[instrument][0])
        for wave_set in waves.values():
            self.assertEqual(len(wave_set), 1)

    def test_a_rare_instrument_leads_no_group(self):
        a = self.analysis()
        mapping = ableton.group_instruments(a, min_notes=99)
        self.assertEqual(mapping, {})  # nothing carries that many notes

    def test_a_low_floor_gives_every_instrument_a_preset(self):
        a = self.analysis()
        mapping = ableton.group_instruments(a, min_notes=1, tolerance=0.0)
        self.assertEqual(len(mapping), len(a.instruments))

    def test_a_wide_tolerance_collapses_more_than_a_narrow_one(self):
        a = analyse(make_vgm(two_instruments()))
        narrow = len(set(ableton.group_instruments(a, tolerance=0.0, min_notes=1).values()))
        wide = len(set(ableton.group_instruments(a, tolerance=10.0, min_notes=1).values()))
        self.assertLessEqual(wide, narrow)


class NameTests(unittest.TestCase):
    def test_the_name_carries_the_wave_and_the_envelope(self):
        self.assertEqual(
            ableton.preset_name("wave-01", (0.1, 526.1, 0.0003162277571, 29.2)),
            "wave-01 a0 d526 s-70 r29",
        )

    def test_the_sustain_reads_as_db_below_the_peak(self):
        self.assertIn(" s0 ", ableton.preset_name("wave-00", (0.1, 100.0, 1.0, 50.0)))
        self.assertIn(" s-12 ", ableton.preset_name("wave-00", (0.1, 100.0, 0.2512, 50.0)))

    def test_the_sustain_never_reads_below_the_floor(self):
        name = ableton.preset_name("wave-00", (0.1, 100.0, ableton.LEVEL_FLOOR, 50.0))
        self.assertIn(f" s{-round(ableton.FLOOR_DB)} ", name)

    def test_a_name_holds_no_instrument_number(self):
        name = ableton.preset_name("wave-03", (23.4, 187.1, 0.2512, 904.3))
        self.assertNotIn("inst", name)
        self.assertTrue(name.startswith("wave-03 "))

    def test_names_sort_by_wave(self):
        values = (0.1, 100.0, 0.5, 50.0)
        names = [ableton.preset_name(w, values) for w in ("wave-02", "wave-00", "wave-01")]
        self.assertEqual(sorted(names), [n for n in sorted(names)])
        self.assertTrue(sorted(names)[0].startswith("wave-00"))

    def test_settings_far_enough_apart_to_survive_grouping_get_their_own_name(self):
        close = (0.1, 200.0, 0.5, 300.0)
        apart = (0.1, 200.0 + 2 * ableton.FRAME_MS + 5, 0.5, 300.0)
        self.assertFalse(ableton.same_preset(close, apart, tolerance=0.0))
        self.assertNotEqual(
            ableton.preset_name("wave-00", close), ableton.preset_name("wave-00", apart)
        )


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
            written = ableton.write_presets(vgm, analysis, wave_dir, out, min_notes=1)
            self.assertEqual(len(written), len(analysis.instruments))
            for row in written:
                self.assertTrue(os.path.exists(os.path.join(out, row[0] + ableton.SUFFIX)))
                self.assertTrue(row[0].startswith(row[1].strip() + " "))

    def test_a_preset_reports_what_it_covers(self):
        vgm, analysis = build_analysis(two_instruments())
        with tempfile.TemporaryDirectory() as root:
            wave_dir = os.path.join(root, "wavs")
            waves.write_files(vgm, waves.extract(vgm), wave_dir)
            written = ableton.write_presets(
                vgm, analysis, wave_dir, os.path.join(root, "out"), min_notes=1
            )
        instruments = sum(row[4] for row in written)
        self.assertEqual(instruments, len(analysis.instruments))
        self.assertTrue(all(row[5] > 0 for row in written))

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
            written = ableton.write_presets(
                vgm, analysis, wave_dir, out, library=library,
                sample_dir="Samples/PCE", min_notes=1,
            )
            with open(os.path.join(out, written[0][0] + ableton.SUFFIX), "rb") as handle:
                blob = handle.read()
            part = ET.fromstring(gzip.decompress(blob)).find(ableton.PART)
            relative = part.find("SampleRef/FileRef/RelativePath").get("Value")
            absolute = part.find("SampleRef/FileRef/Path").get("Value")
        self.assertTrue(relative.startswith("Samples/PCE/wave-"))
        self.assertTrue(absolute.startswith(os.path.abspath(library)))
        self.assertIn(os.path.join("Samples", "PCE"), absolute)

    def test_the_note_floor_drops_instruments_that_fire_once(self):
        vgm, analysis = build_analysis(two_instruments())
        with tempfile.TemporaryDirectory() as root:
            wave_dir = os.path.join(root, "wavs")
            waves.write_files(vgm, waves.extract(vgm), wave_dir)
            out = os.path.join(root, "out")
            few = ableton.write_presets(vgm, analysis, wave_dir, out, min_notes=2)
            every = ableton.write_presets(vgm, analysis, wave_dir, out, min_notes=1)
        self.assertEqual(few, [])
        self.assertEqual(len(every), len(analysis.instruments))


if __name__ == "__main__":
    unittest.main()
