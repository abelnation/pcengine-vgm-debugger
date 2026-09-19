import unittest

from pcevgm import notes
from pcevgm.huc6280 import (
    REG_BALANCE,
    REG_CHANNEL_SELECT,
    REG_CONTROL,
    REG_FREQ_HIGH,
    REG_FREQ_LOW,
    REG_MASTER_BALANCE,
    REG_NOISE,
    REG_WAVE_DATA,
)
from pcevgm.vgm import Command, Gd3, VgmFile, VgmHeader

FRAME = notes.FRAME
RAMP = bytes(range(32))
FLAT = bytes([7] * 32)
C4_DIVIDER = 0x1AC  # about 261 Hz
G4_DIVIDER = 0x11F  # a fifth up, well past a semitone


def write(sample, register, value):
    return Command(0, sample, 0xB9, bytes((register, value)), 0)


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


def setup(channel, wave=RAMP, sample=0):
    """Select a channel, upload a wave and open its balance."""
    out = [write(sample, REG_MASTER_BALANCE, 0xFF), write(sample, REG_CHANNEL_SELECT, channel)]
    out.append(write(sample, REG_CONTROL, 0x00))
    out += [write(sample, REG_WAVE_DATA, value) for value in wave]
    out.append(write(sample, REG_BALANCE, 0xFF))
    return out


def play(channel, sample, divider, amps, gap=FRAME):
    """A note: pitch and first amplitude at `sample`, then one step per gap."""
    out = [
        write(sample, REG_CHANNEL_SELECT, channel),
        write(sample, REG_FREQ_LOW, divider & 0xFF),
        write(sample, REG_FREQ_HIGH, divider >> 8),
        write(sample, REG_CONTROL, 0x80 | amps[0]),
    ]
    for step, amplitude in enumerate(amps[1:], start=1):
        out.append(write(sample + step * gap, REG_CHANNEL_SELECT, channel))
        out.append(write(sample + step * gap, REG_CONTROL, 0x80 | amplitude))
    return out


class InstantTests(unittest.TestCase):
    def test_writes_group_by_sample_time(self):
        commands = [write(0, 1, 2), write(0, 3, 4), write(FRAME, 5, 6)]
        self.assertEqual(
            list(notes.instants(commands)),
            [(0, [(1, 2), (3, 4)]), (FRAME, [(5, 6)])],
        )

    def test_commands_for_other_chips_are_ignored(self):
        other = Command(0, 0, 0x62, b"", FRAME)
        self.assertEqual(list(notes.instants([other, write(0, 1, 2)])), [(0, [(1, 2)])])


class DetectTests(unittest.TestCase):
    def detect(self, commands):
        return notes.detect(make_vgm(commands))

    def test_a_gate_on_starts_a_note(self):
        found = self.detect(setup(0) + play(0, 0, C4_DIVIDER, [31, 20, 0]))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].channel, 0)
        self.assertEqual(found[0].divider, C4_DIVIDER)
        self.assertEqual(found[0].start, 0)

    def test_a_note_ends_when_the_amplitude_reaches_zero(self):
        found = self.detect(setup(0) + play(0, 0, C4_DIVIDER, [31, 20, 0]))
        self.assertEqual(found[0].end, 2 * FRAME)

    def test_a_pitch_reload_starts_a_second_note(self):
        commands = setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 26])
        commands += play(0, 4 * FRAME, G4_DIVIDER, [31, 28, 0])
        found = self.detect(commands)
        self.assertEqual([n.divider for n in found], [C4_DIVIDER, G4_DIVIDER])

    def test_a_pitch_nudge_does_not_start_a_note(self):
        commands = setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 26])
        commands.append(write(4 * FRAME, REG_CHANNEL_SELECT, 0))
        commands.append(write(4 * FRAME, REG_FREQ_LOW, (C4_DIVIDER + 2) & 0xFF))
        self.assertEqual(len(self.detect(commands)), 1)

    def test_an_amplitude_jump_starts_a_note(self):
        found = self.detect(setup(0) + play(0, 0, C4_DIVIDER, [31, 10, 31, 10]))
        self.assertEqual(len(found), 2)
        self.assertEqual([n.start for n in found], [0, 2 * FRAME])

    def test_a_small_amplitude_rise_does_not_start_a_note(self):
        found = self.detect(setup(0) + play(0, 0, C4_DIVIDER, [31, 10, 12, 0]))
        self.assertEqual(len(found), 1)

    def test_two_onsets_inside_one_frame_collapse(self):
        commands = setup(0) + play(0, 0, C4_DIVIDER, [31])
        commands += play(0, FRAME // 2, G4_DIVIDER, [31, 0])
        self.assertEqual(len(self.detect(commands)), 1)

    def test_noise_closes_the_note(self):
        commands = setup(5) + play(5, 0, C4_DIVIDER, [31, 28, 26])
        commands.append(write(2 * FRAME, REG_CHANNEL_SELECT, 5))
        commands.append(write(2 * FRAME, REG_NOISE, 0x9F))
        found = self.detect(commands)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].end, 2 * FRAME)

    def test_dda_produces_no_note(self):
        commands = setup(0)
        commands.append(write(0, REG_CONTROL, 0xC0 | 0x1F))
        self.assertEqual(self.detect(commands), [])

    def test_channels_are_tracked_apart(self):
        commands = setup(0) + setup(3) + play(0, 0, C4_DIVIDER, [31, 0])
        commands += play(3, 0, G4_DIVIDER, [20, 0])
        found = self.detect(commands)
        self.assertEqual(sorted(n.channel for n in found), [0, 3])


class EnvelopeTests(unittest.TestCase):
    def test_the_envelope_is_the_written_amplitude_sequence(self):
        found = notes.detect(make_vgm(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 26, 0])))
        self.assertEqual(notes.envelope_of(found[0]), (31, 28, 26, 0))

    def test_the_envelope_is_capped(self):
        # Alternate by one step: no jump large enough to start another note.
        wobble = [31, 30] * (notes.ENVELOPE_MAX_STEPS + 8)
        found = notes.detect(make_vgm(setup(0) + play(0, 0, C4_DIVIDER, wobble)))
        self.assertEqual(len(found), 1)
        self.assertEqual(len(notes.envelope_of(found[0])), notes.ENVELOPE_MAX_STEPS)

    def test_a_prefix_merges_into_the_longer_envelope(self):
        full = (31, 28, 26, 23, 0)
        mapping = notes.merge_prefixes([full, (31, 28), (31, 28, 26)])
        self.assertEqual(mapping[(31, 28)], full)
        self.assertEqual(mapping[(31, 28, 26)], full)
        self.assertEqual(mapping[full], full)

    def test_a_different_shape_stays_separate(self):
        mapping = notes.merge_prefixes([(31, 28, 26), (31, 29, 26)])
        self.assertEqual(len(set(mapping.values())), 2)


class AnalysisTests(unittest.TestCase):
    def build(self):
        commands = setup(0) + setup(1, wave=FLAT)
        commands += play(0, 0, C4_DIVIDER, [31, 28, 0])
        commands += play(1, 0, C4_DIVIDER, [31, 28, 0])
        commands += play(0, 6 * FRAME, G4_DIVIDER, [31, 28, 0])
        return notes.analyse(make_vgm(commands))

    def test_one_envelope_serves_two_waves_as_two_instruments(self):
        analysis = self.build()
        self.assertEqual(len(analysis.envelopes), 1)
        self.assertEqual(len(analysis.instruments), 2)
        waves = {wave for wave, _ in analysis.instruments}
        self.assertEqual(waves, {"wave-00", "wave-01"})

    def test_the_same_instrument_repeats_across_notes(self):
        analysis = self.build()
        on_zero = [n.instrument for n in analysis.notes if n.channel == 0]
        self.assertEqual(len(set(on_zero)), 1)

    def test_names_are_numbered_and_padded(self):
        analysis = self.build()
        self.assertEqual(analysis.instrument_names[:2], ["inst-00", "inst-01"])
        self.assertEqual(analysis.envelope_names[0], "env-00")

    def test_note_lookup_finds_the_note_covering_a_time(self):
        analysis = self.build()
        self.assertIsNotNone(analysis.note_at(0, FRAME))
        self.assertEqual(analysis.note_at(0, 6 * FRAME).divider, G4_DIVIDER)

    def test_note_lookup_before_the_first_note_is_empty(self):
        analysis = self.build()
        self.assertIsNone(analysis.note_at(0, -1))
        self.assertIsNone(analysis.note_at(4, 0))

    def test_instrument_lookup_matches_the_note(self):
        analysis = self.build()
        note = analysis.note_at(0, FRAME)
        self.assertEqual(analysis.instrument_at(0, FRAME), note.instrument)

    def test_pitch_text_reads_the_divider(self):
        analysis = self.build()
        self.assertTrue(analysis.pitch_text(analysis.notes[0]).startswith("C4"))


if __name__ == "__main__":
    unittest.main()
