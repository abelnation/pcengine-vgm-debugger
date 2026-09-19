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
from pcevgm.notes import FRAME


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

    def test_a_silent_channel_leaves_its_cell_empty(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        cell = rows[0].cells[3]
        self.assertFalse(cell.onset)
        self.assertEqual(tracker.format_cell(cell, wide=True).strip(), "")

    def test_zero_amplitude_prints_once_then_the_cell_empties(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0, 0, 0]))
        self.assertEqual(rows[1].cells[0].amplitude, "1C")
        self.assertEqual(rows[2].cells[0].amplitude, "00")  # the note lets go
        self.assertEqual(tracker.format_cell(rows[3].cells[0], wide=True).strip(), "")
        self.assertEqual(tracker.format_cell(rows[4].cells[0], wide=True).strip(), "")

    def test_the_release_row_starts_no_note(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0, 0]))
        self.assertFalse(rows[2].cells[0].onset)
        self.assertEqual(rows[2].cells[0].note, tracker.NO_NOTE)

    def test_a_channel_that_never_sounds_prints_nothing(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        for row in rows:
            self.assertEqual(tracker.format_cell(row.cells[3], wide=True).strip(), "")

    def test_a_cell_is_the_same_width_empty_or_full(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 0]))
        for wide in (False, True):
            widths = {
                len(tracker.format_cell(cell, wide))
                for row in rows
                for cell in row.cells
            }
            self.assertEqual(widths, {tracker.CELL_WIDE if wide else tracker.CELL_NARROW})

    def test_a_noise_channel_reads_as_silent(self):
        noise = [write(0, REG_CHANNEL_SELECT, 5), write(0, REG_NOISE, 0x9F)]
        commands = ordered(setup(5), play(5, 0, C4_DIVIDER, [31, 28, 26]), noise)
        _, rows = self.build(commands)
        self.assertEqual(tracker.format_cell(rows[0].cells[5], wide=True).strip(), "")

    def test_two_channels_start_on_the_same_row(self):
        commands = ordered(
            setup(0), setup(2),
            play(0, 0, C4_DIVIDER, [31, 0]),
            play(2, 0, G4_DIVIDER, [20, 0]),
        )
        _, rows = self.build(commands)
        self.assertEqual(rows[0].cells[0].instrument, "00")
        self.assertTrue(rows[0].cells[2].onset)

    def test_row_lookup_finds_the_row_covering_a_time(self):
        _, rows = self.build(setup(0) + play(0, 0, C4_DIVIDER, [31, 28, 26, 0]))
        self.assertEqual(tracker.row_at(rows, 0), 0)
        self.assertEqual(tracker.row_at(rows, FRAME), 1)
        self.assertEqual(tracker.row_at(rows, FRAME + 10), 1)
        self.assertEqual(tracker.row_at(rows, 10 ** 9), len(rows) - 1)


def noise(channel, sample, frequency, enabled=True):
    return [
        write(sample, REG_CHANNEL_SELECT, channel),
        write(sample, REG_NOISE, (0x80 if enabled else 0x00) | frequency),
    ]


class NoiseTests(unittest.TestCase):
    def build(self, commands):
        vgm = make_vgm(commands)
        return tracker.build(vgm, analyse(vgm))

    def hit(self, channel=5, frequency=0x1F):
        return ordered(
            setup(channel),
            noise(channel, 0, frequency),
            play(channel, 0, C4_DIVIDER, [31, 28, 0]),
        )

    def test_there_are_two_noise_columns(self):
        self.assertEqual(tracker.NOISE_CHANNELS, (4, 5))

    def test_a_hit_names_the_frequency_and_the_amplitude(self):
        rows = self.build(self.hit())
        cell = rows[0].noise[1]
        self.assertTrue(cell.onset)
        self.assertEqual((cell.frequency, cell.amplitude), ("1F", "1F"))

    def test_a_held_hit_drops_the_frequency(self):
        rows = self.build(self.hit())
        cell = rows[1].noise[1]
        self.assertFalse(cell.onset)
        self.assertEqual((cell.frequency, cell.amplitude), (tracker.NO_VALUE, "1C"))

    def test_a_hit_prints_its_zero_row_then_empties(self):
        commands = ordered(
            setup(5),
            noise(5, 0, 0x1F),
            play(5, 0, C4_DIVIDER, [31, 28, 0, 0]),
        )
        rows = self.build(commands)
        self.assertEqual(rows[2].noise[1].amplitude, "00")
        self.assertFalse(rows[2].noise[1].onset)
        self.assertEqual(tracker.format_noise(rows[3].noise[1]).strip(), "")

    def test_a_frequency_change_starts_a_new_hit(self):
        commands = ordered(
            setup(5),
            noise(5, 0, 0x1F),
            noise(5, FRAME, 0x10),
            play(5, 0, C4_DIVIDER, [31, 28, 26]),
        )
        rows = self.build(commands)
        self.assertEqual(rows[0].noise[1].frequency, "1F")
        self.assertTrue(rows[1].noise[1].onset)
        self.assertEqual(rows[1].noise[1].frequency, "10")

    def test_noise_off_leaves_the_column_empty(self):
        commands = ordered(setup(5), play(5, 0, C4_DIVIDER, [31, 28, 0]))
        rows = self.build(commands)
        self.assertEqual(tracker.format_noise(rows[0].noise[1]).strip(), "")

    def test_channel_four_has_its_own_column(self):
        rows = self.build(self.hit(channel=4))
        self.assertTrue(rows[0].noise[0].onset)
        self.assertEqual(tracker.format_noise(rows[0].noise[1]).strip(), "")

    def test_the_tone_column_stays_empty_while_noise_plays(self):
        rows = self.build(self.hit())
        self.assertEqual(tracker.format_cell(rows[0].cells[5], wide=True).strip(), "")

    def test_a_noise_cell_keeps_its_width(self):
        rows = self.build(self.hit())
        widths = {len(tracker.format_noise(c)) for row in rows for c in row.noise}
        self.assertEqual(widths, {tracker.CELL_NOISE})


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
        cell = tracker.Cell("C-5", "1B", "07", onset=True)
        self.assertEqual(tracker.format_cell(cell, wide=True), "C-5 1B 07")
        self.assertEqual(tracker.format_cell(cell, wide=False), "C-5 1B")

    def test_the_amplitude_column_holds_its_place_in_both_forms(self):
        cell = tracker.Cell("C-5", "1B", "07", onset=True)
        narrow = tracker.format_cell(cell, wide=False)
        wide = tracker.format_cell(cell, wide=True)
        self.assertTrue(wide.startswith(narrow))

    def test_segments_rebuild_the_formatted_row(self):
        _, rows = self.rows()
        for wide in (False, True):
            for row in rows:
                joined = " ".join(
                    segment.text for segment in tracker.row_segments(row, wide)
                )
                self.assertEqual(joined, tracker.format_row(row, wide))

    def test_only_the_row_number_and_the_new_notes_are_active(self):
        _, rows = self.rows()
        segments = tracker.row_segments(rows[0])
        self.assertTrue(segments[0].onset)  # the row number
        # One channel starts a note, contributing its note and amplitude.
        self.assertEqual([s.onset for s in segments[1:]].count(True), 2)

    def test_every_value_gets_its_own_segment(self):
        _, rows = self.rows()
        self.assertEqual(len(tracker.row_segments(rows[0], wide=False)), 1 + 6 * 2 + 2 * 2)
        self.assertEqual(len(tracker.row_segments(rows[0], wide=True)), 1 + 6 * 3 + 2 * 2)

    def test_placeholders_are_marked_and_values_are_not(self):
        _, rows = self.rows()
        sustained = tracker.row_segments(rows[1], wide=True)[1:4]
        self.assertEqual([s.dots for s in sustained], [True, False, True])
        struck = tracker.row_segments(rows[0], wide=True)[1:4]
        self.assertEqual([s.dots for s in struck], [False, False, False])

    def test_a_blank_field_is_not_a_placeholder(self):
        _, rows = self.rows()
        blank = [s for s in tracker.row_segments(rows[0]) if not s.text.strip()]
        self.assertTrue(blank)
        self.assertFalse(any(s.dots for s in blank))

    def test_a_row_with_no_note_has_nothing_active(self):
        _, rows = self.rows()
        self.assertFalse(any(s.onset for s in tracker.row_segments(rows[1])))

    def test_a_segment_carries_its_amplitude(self):
        _, rows = self.rows()
        segments = tracker.row_segments(rows[0])
        self.assertEqual(segments[0].level, -1)  # the row number has none
        self.assertEqual(segments[1].level, 31)  # channel 0 at full
        self.assertEqual(segments[7].level, -1)  # a silent channel

    def test_the_amplitude_falls_with_the_envelope(self):
        _, rows = self.rows()
        levels = [tracker.row_segments(row)[2].level for row in rows]
        self.assertEqual(levels[:3], [31, 28, 0])

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
