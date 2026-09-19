import unittest

from pcevgm.player import Timeline, build_descriptions
from pcevgm.vgm import Command, Gd3, VgmFile, VgmHeader


def make_vgm(commands, total_samples=0, loop_index=None):
    header = VgmHeader(
        version=0x161,
        eof_offset=0,
        gd3_offset=0,
        total_samples=total_samples,
        loop_offset=0,
        loop_samples=0,
        rate=60,
        data_offset=0x100,
        clocks={"HuC6280": 3_579_545},
    )
    return VgmFile("test.vgm", header, Gd3(), commands, [], loop_index)


def write(sample, register, value, offset=0):
    return Command(offset, sample, 0xB9, bytes((register, value)), 0)


def wait(sample, samples=735, offset=0):
    return Command(offset, sample, 0x62, b"", samples)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.commands = [
            write(0, 0x00, 0),
            write(0, 0x04, 0x9F),
            wait(0),
            write(735, 0x04, 0x00),
            wait(735),
            write(1470, 0x00, 1),
            write(1470, 0x04, 0x8A),
            Command(0, 1470, 0x66, b"", 0),
        ]
        self.timeline = Timeline(make_vgm(self.commands, total_samples=1470))

    def test_seek_applies_every_earlier_write(self):
        self.timeline.seek_sample(0)
        self.assertTrue(self.timeline.state.channels[0].enabled)
        self.assertEqual(self.timeline.state.channels[0].amplitude, 0x1F)

    def test_seek_backwards_rebuilds_state(self):
        self.timeline.seek_sample(1470)
        self.assertTrue(self.timeline.state.channels[1].enabled)
        self.timeline.seek_sample(735)
        self.assertFalse(self.timeline.state.channels[0].enabled)
        self.assertFalse(self.timeline.state.channels[1].enabled)

    def test_seek_clamps_to_the_end(self):
        self.timeline.seek_sample(10 ** 9)
        self.assertEqual(self.timeline.sample, 1470)

    def test_goto_index_sets_time_from_the_previous_command(self):
        self.timeline.goto_index(3)
        self.assertEqual(self.timeline.sample, 735)
        self.assertEqual(self.timeline.index, 3)

    def test_step_write_moves_one_register_write(self):
        self.timeline.goto_index(0)
        self.timeline.step_write(1)
        self.assertEqual(self.timeline.index, 1)
        self.timeline.step_write(1)
        self.assertEqual(self.timeline.index, 2)
        self.timeline.step_write(-1)
        self.assertEqual(self.timeline.index, 1)

    def test_end_sample_falls_back_to_the_stream(self):
        timeline = Timeline(make_vgm([wait(0), wait(735)]))
        self.assertEqual(timeline.end_sample, 1470)

    def test_loop_sample_comes_from_the_loop_command(self):
        timeline = Timeline(make_vgm(self.commands, 1470, loop_index=3))
        self.assertEqual(timeline.loop_sample, 735)


class DescriptionTests(unittest.TestCase):
    def test_write_text_uses_the_channel_selected_before_it(self):
        vgm = make_vgm([write(0, 0x00, 2), write(0, 0x04, 0x9F)])
        lines = build_descriptions(vgm)
        self.assertEqual(lines[0], "select channel 2")
        self.assertIn("ch2", lines[1])
        self.assertIn("amp=31", lines[1])


if __name__ == "__main__":
    unittest.main()
