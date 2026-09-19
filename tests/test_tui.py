import unittest

from pcevgm import tui
from pcevgm.huc6280 import NUM_CHANNELS, WAVE_LENGTH, plot_width
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

    def test_every_prefix_line_is_the_same_fixed_width(self):
        state = self.debugger.timeline.state
        for index in range(NUM_CHANNELS):
            head, detail, extra, _ = self.debugger._channel_text(index, state)
            self.assertEqual(len(head), tui.HEAD_WIDTH, index)
            self.assertEqual(len(detail), tui.HEAD_WIDTH, index)
            if extra:
                self.assertEqual(len(extra), tui.HEAD_WIDTH, index)

    def test_the_second_line_names_instrument_then_wave_then_envelope(self):
        state = self.debugger.timeline.state
        _, detail, _, _ = self.debugger._channel_text(0, state)
        # A placeholder reads "wave  --", so match on position, not on split().
        found = [detail.index(name) for name in ("inst", "wave", "env")]
        self.assertEqual(found, sorted(found), detail)

    def test_the_third_line_carries_the_noise_register(self):
        state = self.debugger.timeline.state
        self.assertEqual(self.debugger._channel_text(0, state)[2], "")
        extra = self.debugger._channel_text(4, state)[2]
        self.assertIn("noise", extra)

    def test_the_envelope_sits_after_the_wave_plot(self):
        self.assertEqual(
            tui.ENVELOPE_LEFT,
            tui.HEAD_WIDTH + plot_width(WAVE_LENGTH) + tui.ENVELOPE_GAP,
        )

    def test_the_wave_plot_has_one_row_per_key_row(self):
        state = self.debugger.timeline.state
        _, _, _, plot = self.debugger._channel_text(0, state)
        self.assertEqual(len(plot), tui.WAVE_ROWS)
        self.assertTrue(all(len(row) == plot_width(WAVE_LENGTH) for row in plot))

    def test_the_tracker_sits_after_the_capped_envelope(self):
        self.assertEqual(
            tui.TRACKER_LEFT,
            tui.ENVELOPE_LEFT + tui.ENVELOPE_WIDTH + tui.TRACKER_GAP,
        )
        self.assertEqual(
            tui.TRACKER_MIN_COLUMNS, tui.TRACKER_LEFT + tui.TRACKER_WIDTH + 1
        )

    def test_the_tracker_gives_way_on_a_narrow_terminal(self):
        self.assertFalse(self.debugger._tracker_fits(tui.TRACKER_MIN_COLUMNS - 1))
        self.assertTrue(self.debugger._tracker_fits(tui.TRACKER_MIN_COLUMNS))

    def test_the_envelope_cap_is_narrower_than_the_longest_envelope(self):
        from pcevgm.huc6280 import plot_width
        from pcevgm.notes import ENVELOPE_MAX_STEPS

        self.assertLess(tui.ENVELOPE_WIDTH, plot_width(ENVELOPE_MAX_STEPS))

    def test_the_grid_has_a_row_for_every_tick(self):
        from pcevgm import tracker

        self.assertEqual(
            len(self.debugger.rows), len(tracker.ticks(self.debugger.vgm))
        )

    def test_a_channel_with_no_note_has_no_envelope_plot(self):
        self.assertEqual(self.debugger._envelope_plot(3), (None, 0))


if __name__ == "__main__":
    unittest.main()
