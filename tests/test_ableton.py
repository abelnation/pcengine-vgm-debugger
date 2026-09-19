import gzip
import math
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pcevgm import ableton, waves
from pcevgm.huc6280 import WAVE_LENGTH


def wave_dump(root, count=2):
    """A folder of single cycle wave files, as --extract-waves leaves it."""
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        stem = os.path.join(root, f"wave-{index:02d}")
        waves.write_wav(stem + waves.WAV_SUFFIX, [index] * WAVE_LENGTH, waves.cycle_rate())
        waves.write_wav(stem + waves.LONG_WAV_SUFFIX, [index] * 100, waves.WAV_RATE)
    return root


class EnvelopeTests(unittest.TestCase):
    def test_every_envelope_has_a_distinct_name(self):
        self.assertEqual(len(set(ableton.NAMES)), len(ableton.STANDARD))

    def test_the_three_named_shapes_are_present(self):
        for name in ("hold", "decay", "tail"):
            self.assertIn(name, ableton.NAMES)

    def test_hold_sustains_at_full(self):
        envelope = next(e for e in ableton.STANDARD if e.name == "hold")
        _, _, sustain, _ = ableton.values(envelope)
        self.assertEqual(sustain, 1.0)

    def test_a_silent_sustain_writes_simplers_floor(self):
        envelope = next(e for e in ableton.STANDARD if e.name == "stab")
        _, _, sustain, _ = ableton.values(envelope)
        self.assertEqual(sustain, ableton.LEVEL_FLOOR)

    def test_the_percussive_shapes_hold_more_tone_in_turn(self):
        levels = [
            ableton.values(next(e for e in ableton.STANDARD if e.name == name))[2]
            for name in ("stab", "pluck", "tail")
        ]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(len(set(levels)), 3)

    def test_no_two_envelopes_are_the_same_preset(self):
        settings = [ableton.values(envelope) for envelope in ableton.STANDARD]
        self.assertEqual(len(set(settings)), len(settings))

    def test_a_level_between_reads_as_db(self):
        envelope = next(e for e in ableton.STANDARD if e.name == "tail")
        _, _, sustain, _ = ableton.values(envelope)
        self.assertAlmostEqual(20 * math.log10(sustain), envelope.sustain_db, places=6)

    def test_every_setting_lands_inside_simpler_limits(self):
        for envelope in ableton.STANDARD:
            attack, decay, sustain, release = ableton.values(envelope)
            self.assertGreaterEqual(attack, ableton.ATTACK_RANGE[0])
            self.assertLessEqual(attack, ableton.ATTACK_RANGE[1])
            for time in (decay, release):
                self.assertGreaterEqual(time, ableton.TIME_RANGE[0])
                self.assertLessEqual(time, ableton.TIME_RANGE[1])
            self.assertGreaterEqual(sustain, ableton.LEVEL_FLOOR)
            self.assertLessEqual(sustain, 1.0)

    def test_no_attack_means_simplers_minimum_not_zero(self):
        # Simpler has no zero there, so 0.1 ms is how it spells "none".
        self.assertEqual(ableton.NONE, ableton.ATTACK_RANGE[0])
        instant = [e for e in ableton.STANDARD if e.name != "swell"]
        self.assertTrue(all(e.attack == ableton.NONE for e in instant))


class ChoiceTests(unittest.TestCase):
    def test_no_names_means_all_of_them(self):
        self.assertEqual(ableton.chosen(), list(ableton.STANDARD))
        self.assertEqual(ableton.chosen(()), list(ableton.STANDARD))

    def test_a_selection_keeps_the_standard_order(self):
        picked = ableton.chosen(["tail", "hold"])
        self.assertEqual([e.name for e in picked], ["hold", "tail"])

    def test_whitespace_is_forgiven(self):
        self.assertEqual([e.name for e in ableton.chosen([" hold ", ""])], ["hold"])

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(ValueError):
            ableton.chosen(["nope"])


class WaveFileTests(unittest.TestCase):
    def test_the_long_preview_is_not_mistaken_for_a_cycle(self):
        with tempfile.TemporaryDirectory() as root:
            found = ableton.wave_files(wave_dump(root, count=3))
        self.assertEqual(len(found), 3)
        self.assertTrue(all(not p.endswith(waves.LONG_WAV_SUFFIX) for p in found))

    def test_an_empty_folder_yields_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ableton.wave_files(root), [])


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

    def test_the_library_gives_the_absolute_path(self):
        _, absolute = ableton.sample_paths(
            self.WAV, library=self.LIBRARY, sample_dir="Samples/PCE"
        )
        self.assertEqual(
            absolute,
            os.path.join(os.path.abspath(self.LIBRARY), "Samples", "PCE", "wave-01.wav"),
        )

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


class NameTests(unittest.TestCase):
    def test_a_preset_is_named_for_its_wave_and_envelope(self):
        envelope = ableton.STANDARD[0]
        name = ableton.preset_name(os.path.join("x", "wave-03.wav"), envelope)
        self.assertEqual(name, f"wave-03 {envelope.name}")

    def test_names_sort_by_wave(self):
        envelope = ableton.STANDARD[0]
        names = [ableton.preset_name(f"wave-{i:02d}.wav", envelope) for i in (2, 0, 1)]
        self.assertTrue(sorted(names)[0].startswith("wave-00"))


class TemplateTests(unittest.TestCase):
    def test_the_template_ships_with_the_package(self):
        self.assertTrue(os.path.exists(ableton.TEMPLATE))

    def test_the_template_names_no_machine(self):
        with open(ableton.TEMPLATE, "rb") as handle:
            text = gzip.decompress(handle.read()).decode()
        self.assertNotIn("/Users/", text)


class BuildTests(unittest.TestCase):
    def preset(self, settings=(0.1, 200.0, 0.5, 300.0)):
        with tempfile.TemporaryDirectory() as out:
            wav = os.path.join(out, "wave-00.wav")
            waves.write_wav(wav, [0] * WAVE_LENGTH, waves.cycle_rate())
            blob = ableton.build("wave-00 pluck", wav, settings, "Samples/wave-00.wav")
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
        self.assertEqual(
            part.find("SampleRef/DefaultSampleRate").get("Value"), str(waves.cycle_rate())
        )

    def test_the_crc_is_zeroed(self):
        part = self.preset().find(ableton.PART)
        self.assertEqual(part.find("SampleRef/FileRef/OriginalCrc").get("Value"), "0")

    def test_the_envelope_carries_the_values_given(self):
        shape = self.preset((0.1, 200.0, 0.5, 300.0)).find(ableton.ENVELOPE)
        self.assertEqual(float(shape.find("DecayTime/Manual").get("Value")), 200.0)
        self.assertEqual(float(shape.find("SustainLevel/Manual").get("Value")), 0.5)
        self.assertEqual(float(shape.find("ReleaseTime/Manual").get("Value")), 300.0)

    def test_a_missing_field_is_reported(self):
        with self.assertRaises(KeyError):
            ableton._set(self.preset(), "NoSuchField", 1)


class WriteTests(unittest.TestCase):
    def test_every_envelope_lands_on_every_wave(self):
        with tempfile.TemporaryDirectory() as root:
            dump = wave_dump(os.path.join(root, "wavs"), count=3)
            out = os.path.join(root, "ableton")
            written = ableton.write_presets(dump, out)
        self.assertEqual(len(written), 3 * len(ableton.STANDARD))
        self.assertEqual(len({name for name, _, _ in written}), len(written))

    def test_a_selection_writes_only_what_was_asked(self):
        with tempfile.TemporaryDirectory() as root:
            dump = wave_dump(os.path.join(root, "wavs"), count=2)
            out = os.path.join(root, "ableton")
            written = ableton.write_presets(dump, out, names=["hold", "tail"])
            files = sorted(os.listdir(out))
        self.assertEqual(len(written), 4)
        self.assertEqual(
            files,
            ["wave-00 hold.adv", "wave-00 tail.adv", "wave-01 hold.adv", "wave-01 tail.adv"],
        )

    def test_the_sample_dir_reaches_the_written_preset(self):
        with tempfile.TemporaryDirectory() as root:
            dump = wave_dump(os.path.join(root, "wavs"))
            out = os.path.join(root, "ableton")
            library = os.path.join(root, "library")
            ableton.write_presets(
                dump, out, library=library, sample_dir="Samples/PCE", names=["hold"]
            )
            with open(os.path.join(out, "wave-00 hold.adv"), "rb") as handle:
                part = ET.fromstring(gzip.decompress(handle.read())).find(ableton.PART)
            relative = part.find("SampleRef/FileRef/RelativePath").get("Value")
            absolute = part.find("SampleRef/FileRef/Path").get("Value")
        self.assertEqual(relative, "Samples/PCE/wave-00.wav")
        self.assertTrue(absolute.startswith(os.path.abspath(library)))

    def test_an_unknown_envelope_stops_the_write(self):
        with tempfile.TemporaryDirectory() as root:
            dump = wave_dump(os.path.join(root, "wavs"))
            with self.assertRaises(ValueError):
                ableton.write_presets(dump, os.path.join(root, "out"), names=["nope"])


if __name__ == "__main__":
    unittest.main()
