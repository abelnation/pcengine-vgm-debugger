import os
import tempfile
import unittest

from pcevgm import tracker
from pcevgm.huc6280 import NUM_CHANNELS
from pcevgm.notes import FRAME, analyse
from tests.test_notes import (
    C4_DIVIDER,
    G4_DIVIDER,
    make_vgm,
    ordered,
    play,
    setup,
    write,
)
from pcevgm.huc6280 import REG_CHANNEL_SELECT, REG_NOISE


class TickTests(unittest.TestCase):
    def test_writes_inside_one_frame_make_one_tick(self):
        commands = [write(0, 1, 2), write(1, 3, 4), write(2, 5, 6)]
        self.assertEqual(tracker.ticks(make_vgm(commands)), [0])

    def test_a_frame_gap_starts_a_new_tick(self):
        commands = [write(0, 1, 2), write(FRAME, 3, 4), write(2 * FRAME, 5, 6)]
        self.assertEqual(tracker.ticks(make_vgm(commands)), [0, FRAME, 2 * FRAME])

    def test_the_driver_jitter_stays_inside_one_tick(self):
        # Real gaps land between 732 and 738 samples, never near half a frame.
        commands = [write(0, 1, 2), write(FRAME // 2, 3, 4), write(FRAME + 3, 5, 6)]
        self.assertEqual(tracker.ticks(make_vgm(commands)), [0, FRAME + 3])

    def test_an_empty_stream_has_no_ticks(self):
        self.assertEqual(tracker.ticks(make_vgm([])), [])


class NoteNameTests(unittest.TestCase):
    def test_natural_and_sharp_spellings(self):
        self.assertEqual(tracker.note_name(261.63), "C-4")
        self.assertEqual(tracker.note_name(277.18), "C#4")
        self.assertEqual(tracker.note_name(440.0), "A-4")

    def test_a_pitch_out_of_range_has_no_name(self):
        self.assertEqual(tracker.note_name(0), tracker.NO_NOTE)


class BuildTests(unittest.TestCase):
    def build(self, commands):
        vgm = make_vgm(commands)
        return vgm, tracker.build(vgm, analyse(vgm))

    def test_one_row_per_tick_with_one_cell_per_channel(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        self.assertEqual(len(rows), len(tracker.ticks(make_vgm(
            setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0])))))
        for row in rows:
            self.assertEqual(len(row.cells), NUM_CHANNELS)

    def test_an_onset_names_the_note_and_the_instrument(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        cell = rows[0].cells[0]
        self.assertEqual(cell.note, "C-4")
        self.assertEqual(cell.instrument, "00")
        self.assertEqual(cell.amplitude, "1F")

    def test_a_sustained_row_carries_the_amplitude_only(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        cell = rows[1].cells[0]
        self.assertEqual(cell.note, tracker.NO_NOTE)
        self.assertEqual(cell.instrument, tracker.NO_VALUE)
        self.assertEqual(cell.amplitude, "1C")

    def test_a_silent_channel_reads_as_silent(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        cell = rows[0].cells[3]
        self.assertEqual(
            (cell.note, cell.instrument, cell.amplitude),
            (tracker.SILENT, tracker.NO_VALUE, tracker.NO_VALUE),
        )

    def test_a_noise_channel_reads_as_silent(self):
        noise = [write(0, REG_CHANNEL_SELECT, 5), write(0, REG_NOISE, 0x9F)]
        commands = ordered(setup(5), play(5, 0, C4_DIVIDER, [31, 28, 26]), noise)
        _, rows = self.build(commands)
        self.assertEqual(rows[0].cells[5].note, tracker.SILENT)

    def test_two_channels_start_on_the_same_row(self):
        commands = ordered(
            setup(0), setup(2),
            play(0, 0, C4_DIVIDER, [31, 0]),
            play(2, 0, G4_DIVIDER, [20, 0]),
        )
        _, rows = self.build(commands)
        self.assertEqual(rows[0].cells[0].instrument, "00")
        self.assertNotEqual(rows[0].cells[2].instrument, tracker.NO_VALUE)

    def test_row_lookup_finds_the_row_covering_a_time(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 26, 0]))
        self.assertEqual(tracker.row_at(rows, 0), 0)
        self.assertEqual(tracker.row_at(rows, FRAME), 1)
        self.assertEqual(tracker.row_at(rows, FRAME + 10), 1)
        self.assertEqual(tracker.row_at(rows, 10 ** 9), len(rows) - 1)


class FormatTests(unittest.TestCase):
    def rows(self):
        vgm = make_vgm(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        return vgm, tracker.build(vgm, analyse(vgm))

    def test_the_narrow_row_matches_the_panel_width(self):
        _, rows = self.rows()
        self.assertEqual(len(tracker.format_row(rows[0])), tracker.panel_width())
        self.assertEqual(len(tracker.format_header()), tracker.panel_width())

    def test_the_wide_row_matches_the_wide_width(self):
        _, rows = self.rows()
        self.assertEqual(len(tracker.format_row(rows[0], wide=True)),
                         tracker.panel_width(wide=True))
        self.assertEqual(len(tracker.format_header(wide=True)),
                         tracker.panel_width(wide=True))

    def test_the_narrow_cell_drops_the_instrument(self):
        _, rows = self.rows()
        self.assertNotIn("00", tracker.format_cell(rows[0].cells[0], wide=False))
        self.assertIn("00", tracker.format_cell(rows[0].cells[0], wide=True))

    def test_the_cell_reads_note_then_amplitude_then_instrument(self):
        cell = tracker.Cell("C-5", "07", "1B")
        self.assertEqual(tracker.format_cell(cell, wide=True), "C-5 1B 07")
        self.assertEqual(tracker.format_cell(cell, wide=False), "C-5 1B")

    def test_the_amplitude_column_holds_its_place_in_both_forms(self):
        cell = tracker.Cell("C-5", "07", "1B")
        narrow = tracker.format_cell(cell, wide=False)
        wide = tracker.format_cell(cell, wide=True)
        self.assertTrue(wide.startswith(narrow))

    def test_the_dump_holds_a_line_per_row(self):
        vgm, rows = self.rows()
        with tempfile.TemporaryDirectory() as out:
            path = os.path.join(out, "grid.txt")
            tracker.dump(vgm, rows, path)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        body = [line for line in text.split("\n") if line.startswith("00:")]
        self.assertEqual(len(body), len(rows))
        self.assertIn(vgm.path, text)


if __name__ == "__main__":
    unittest.main()
