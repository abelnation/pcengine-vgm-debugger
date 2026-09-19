import unittest

from pcevgm import keyboard
from pcevgm.huc6280 import (
    DEFAULT_CLOCK,
    REG_BALANCE,
    REG_CHANNEL_SELECT,
    REG_CONTROL,
    REG_FREQ_HIGH,
    REG_FREQ_LOW,
    REG_MASTER_BALANCE,
    REG_NOISE,
    HuC6280State,
)

C1, C4, CSHARP4, C5, C6 = 32.703, 261.63, 277.18, 523.25, 1046.5
LOUD = 0x80 | 0x1F


def state_with(pitches, control=LOUD, balance=0xFF, master=0xFF):
    """A chip state with the named channels playing the named pitches."""
    state = HuC6280State(DEFAULT_CLOCK)
    state.write(REG_MASTER_BALANCE, master)
    for channel, hz in pitches.items():
        divider = round(DEFAULT_CLOCK / (32 * hz)) if hz else 0
        state.write(REG_CHANNEL_SELECT, channel)
        state.write(REG_FREQ_LOW, divider & 0xFF)
        state.write(REG_FREQ_HIGH, divider >> 8)
        state.write(REG_BALANCE, balance)
        state.write(REG_CONTROL, control)
    return state


class LayoutTests(unittest.TestCase):
    def test_width_and_key_count(self):
        self.assertEqual(keyboard.WIDTH, keyboard.OCTAVES * keyboard.OCTAVE_CELLS)
        self.assertEqual(len(keyboard.KEYS), keyboard.SEMITONES)

    def test_every_semitone_has_a_key(self):
        self.assertEqual(sorted(keyboard.KEYS), list(range(keyboard.SEMITONES)))

    def test_each_layer_is_key_rows_tall(self):
        for offset, cells in keyboard.KEYS.items():
            rows = {row for row, _ in cells}
            self.assertEqual(len(rows), keyboard.KEY_ROWS, offset)
            if offset % 12 in keyboard.BLACK_STEPS:
                self.assertTrue(all(row < keyboard.FIRST_WHITE_ROW for row in rows))
            else:
                self.assertTrue(all(row >= keyboard.FIRST_WHITE_ROW for row in rows))

    def test_white_keys_hold_four_cells_and_black_keys_two(self):
        for offset, cells in keyboard.KEYS.items():
            expected = 1 if offset % 12 in keyboard.BLACK_STEPS else keyboard.CELLS_PER_WHITE
            self.assertEqual(len(cells), expected * keyboard.KEY_ROWS, offset)

    def test_cells_stay_inside_the_board(self):
        for cells in keyboard.KEYS.values():
            for row, column in cells:
                self.assertTrue(0 <= row < keyboard.TOTAL_ROWS)
                self.assertTrue(0 <= column < keyboard.WIDTH)

    def test_no_two_keys_share_a_cell(self):
        seen = set()
        for cells in keyboard.KEYS.values():
            for cell in cells:
                self.assertNotIn(cell, seen)
                seen.add(cell)

    def test_black_cells_follow_the_piano_pattern(self):
        first = sorted(c for c in keyboard.BLACK_CELLS if c < keyboard.OCTAVE_CELLS)
        self.assertEqual(first, [1, 3, 7, 9, 11])

    def test_labels_mark_each_octave(self):
        text = keyboard.labels()
        self.assertEqual(len(text), keyboard.WIDTH)
        for octave in range(keyboard.OCTAVES):
            start = octave * keyboard.OCTAVE_CELLS
            self.assertEqual(text[start : start + 2], f"C{octave + 1}")


class SoundingTests(unittest.TestCase):
    def test_a_loud_channel_maps_to_its_semitone(self):
        found = keyboard.sounding(state_with({0: C4}))
        self.assertEqual(found, {0: 36})  # C4 is MIDI 60, LOW_MIDI is 24

    def test_a_disabled_channel_is_silent(self):
        self.assertEqual(keyboard.sounding(state_with({0: C4}, control=0x1F)), {})

    def test_a_dda_channel_has_no_pitch(self):
        self.assertEqual(keyboard.sounding(state_with({0: C4}, control=0xDF)), {})

    def test_a_noise_channel_has_no_pitch(self):
        state = state_with({4: C4})
        state.write(REG_CHANNEL_SELECT, 4)
        state.write(REG_NOISE, 0x9F)
        self.assertEqual(keyboard.sounding(state), {})

    def test_noise_on_a_low_channel_does_not_silence_it(self):
        state = state_with({3: C4})
        state.write(REG_CHANNEL_SELECT, 3)
        state.write(REG_NOISE, 0x9F)  # channels below 4 have no noise
        self.assertEqual(keyboard.sounding(state), {3: 36})

    def test_a_quiet_channel_is_left_out(self):
        quiet = state_with({0: C4}, control=0x80, balance=0x00, master=0x00)
        self.assertEqual(keyboard.sounding(quiet), {})

    def test_pitches_off_the_keyboard_keep_their_offset(self):
        self.assertEqual(keyboard.sounding(state_with({0: C6})), {0: 60})
        self.assertLess(keyboard.sounding(state_with({0: 0}))[0], 0)

    def test_unpitched_names_the_reason(self):
        state = state_with({0: C4}, control=0xDF)
        state.write(REG_CHANNEL_SELECT, 5)
        state.write(REG_FREQ_LOW, 0x10)
        state.write(REG_BALANCE, 0xFF)
        state.write(REG_CONTROL, LOUD)
        state.write(REG_NOISE, 0x9F)
        self.assertEqual(keyboard.unpitched(state), [(0, "dda"), (5, "noise")])


class RenderTests(unittest.TestCase):
    C4_KEY, CSHARP4_KEY = 36, 37

    def key_cells(self, state, offset):
        rows = keyboard.render(state)
        return [rows[row][column] for row, column in keyboard.KEYS[offset]]

    def test_render_returns_one_list_per_character_row(self):
        rows = keyboard.render(HuC6280State())
        self.assertEqual(len(rows), keyboard.TOTAL_ROWS)
        for cells in rows:
            self.assertEqual(len(cells), keyboard.WIDTH)

    def test_an_idle_keyboard_has_no_channel_on_any_cell(self):
        rows = keyboard.render(HuC6280State())
        self.assertTrue(all(cell.channel is None for cells in rows for cell in cells))

    def test_black_cells_are_marked_on_every_upper_row(self):
        rows = keyboard.render(HuC6280State())
        for row in range(keyboard.FIRST_WHITE_ROW):
            marked = {index for index, cell in enumerate(rows[row]) if cell.black}
            self.assertEqual(marked, set(keyboard.BLACK_CELLS))

    def test_one_channel_fills_the_whole_key_and_is_named_once(self):
        cells = self.key_cells(state_with({1: C4}), self.C4_KEY)
        self.assertEqual([cell.channel for cell in cells], [1] * len(cells))
        self.assertEqual([cell.char for cell in cells].count("1"), 1)
        self.assertEqual(cells[0].char, "1")

    def test_a_black_key_fills_both_of_its_rows(self):
        cells = self.key_cells(state_with({2: CSHARP4}), self.CSHARP4_KEY)
        self.assertEqual(len(cells), keyboard.KEY_ROWS)
        self.assertEqual([cell.channel for cell in cells], [2, 2])
        rows = keyboard.render(state_with({2: CSHARP4}))
        white = rows[keyboard.FIRST_WHITE_ROW] + rows[keyboard.TOTAL_ROWS - 1]
        self.assertTrue(all(cell.channel is None for cell in white))

    def test_two_channels_split_the_key_in_half(self):
        cells = self.key_cells(state_with({0: C4, 4: C4}), self.C4_KEY)
        half = len(cells) // 2
        self.assertEqual([cell.channel for cell in cells], [0] * half + [4] * half)
        self.assertEqual([cell.char for cell in cells], ["0", " ", "4", " "])

    def test_two_channels_on_a_black_key_take_a_row_each(self):
        cells = self.key_cells(state_with({2: CSHARP4, 5: CSHARP4}), self.CSHARP4_KEY)
        self.assertEqual([cell.channel for cell in cells], [2, 5])
        self.assertEqual([cell.char for cell in cells], ["2", "5"])

    def test_four_channels_take_a_cell_each(self):
        state = state_with({0: C4, 1: C4, 2: C4, 3: C4})
        cells = self.key_cells(state, self.C4_KEY)
        self.assertEqual([cell.channel for cell in cells], [0, 1, 2, 3])
        self.assertEqual([cell.char for cell in cells], ["0", "1", "2", "3"])

    def test_more_channels_than_cells_show_the_overflow_mark(self):
        state = state_with({index: C4 for index in range(5)})
        cells = self.key_cells(state, self.C4_KEY)
        self.assertEqual(cells[-1].char, keyboard.MORE_MARK)
        self.assertEqual([cell.char for cell in cells[:-1]], ["0", "1", "2"])

    def test_a_pitch_above_the_keyboard_marks_the_right_edge(self):
        rows = keyboard.render(state_with({3: C6}))
        cell = rows[keyboard.FIRST_WHITE_ROW][-1]
        self.assertEqual((cell.char, cell.channel), (keyboard.ABOVE_MARK, 3))

    def test_a_pitch_below_the_keyboard_marks_the_left_edge(self):
        rows = keyboard.render(state_with({3: 0}))
        cell = rows[keyboard.FIRST_WHITE_ROW][0]
        self.assertEqual((cell.char, cell.channel), (keyboard.BELOW_MARK, 3))

    def test_the_lowest_key_on_the_board_is_not_an_edge_mark(self):
        cells = self.key_cells(state_with({0: C1}), 0)
        self.assertEqual(cells[0].char, "0")
        self.assertEqual(cells[0].channel, 0)


if __name__ == "__main__":
    unittest.main()
