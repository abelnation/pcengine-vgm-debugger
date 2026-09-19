import unittest

from pcevgm import tui
from pcevgm.huc6280 import NUM_CHANNELS, WAVE_LENGTH
from pcevgm.vgm import Command, Gd3, VgmFile, VgmHeader


def make_vgm(commands=()):
    header = VgmHeader(
        version=0x161,
        eof_offset=0,
        gd3_offset=0,
        total_samples=735,
        loop_offset=0,
        loop_samples=0,
        rate=60,
        data_offset=0x100,
        clocks={"HuC6280": 3_579_545},
    )
    return VgmFile("test.vgm", header, Gd3(), list(commands), [], None)


class LayoutTests(unittest.TestCase):
    """The meters and the envelope plot only line up if the widths agree."""

    def setUp(self):
        select = Command(0, 0, 0xB9, bytes((0x00, 0)), 0)
        control = Command(0, 0, 0xB9, bytes((0x04, 0x9F)), 0)
        self.debugger = tui.Debugger(make_vgm([select, control]))

    def test_head_and_detail_are_the_same_fixed_width(self):
        state = self.debugger.timeline.state
        for index in range(NUM_CHANNELS):
            head, detail, _ = self.debugger._channel_text(index, state)
            self.assertEqual(len(head), tui.HEAD_WIDTH, index)
            self.assertEqual(len(detail), tui.HEAD_WIDTH, index)

    def test_the_envelope_sits_after_the_wave_plot(self):
        self.assertEqual(
            tui.ENVELOPE_LEFT, tui.HEAD_WIDTH + WAVE_LENGTH + tui.ENVELOPE_GAP
        )

    def test_the_wave_plot_has_one_row_per_key_row(self):
        state = self.debugger.timeline.state
        _, _, plot = self.debugger._channel_text(0, state)
        self.assertEqual(len(plot), tui.WAVE_ROWS)
        self.assertTrue(all(len(row) == WAVE_LENGTH for row in plot))

    def test_a_channel_with_no_note_has_no_envelope_plot(self):
        self.assertEqual(self.debugger._envelope_plot(3), (None, 0))


if __name__ == "__main__":
    unittest.main()
