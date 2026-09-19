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

    def test_white_keys_take_two_lower_cells_and_black_keys_one_upper(self):
        for offset, (row, start, count) in keyboard.KEYS.items():
            if offset % 12 in keyboard.BLACK_STEPS:
                self.assertEqual((row, count), (keyboard.UPPER, 1), offset)
            else:
                self.assertEqual((row, count), (keyboard.LOWER, 2), offset)
            self.assertLessEqual(start + count, keyboard.WIDTH)

    def test_no_two_keys_share_a_cell(self):
        seen = set()
        for row, start, count in keyboard.KEYS.values():
            for step in range(count):
                self.assertNotIn((row, start + step), seen)
                seen.add((row, start + step))

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
    def cells(self, state):
        return keyboard.render(state)

    def test_an_idle_keyboard_has_no_channel_on_any_cell(self):
        upper, lower = self.cells(HuC6280State())
        self.assertTrue(all(cell.channel is None for cell in upper + lower))

    def test_black_cells_are_marked_on_the_upper_row(self):
        upper, _ = self.cells(HuC6280State())
        marked = {index for index, cell in enumerate(upper) if cell.black}
        self.assertEqual(marked, set(keyboard.BLACK_CELLS))

    def test_a_white_key_colours_both_cells_and_names_the_channel(self):
        _, lower = self.cells(state_with({1: C4}))
        row, start, count = keyboard.KEYS[36]
        self.assertEqual(row, keyboard.LOWER)
        self.assertEqual([lower[start + s].channel for s in range(count)], [1, 1])
        self.assertEqual(lower[start].char, "1")

    def test_a_black_key_colours_its_upper_cell(self):
        upper, lower = self.cells(state_with({2: CSHARP4}))
        row, start, count = keyboard.KEYS[37]
        self.assertEqual((row, count), (keyboard.UPPER, 1))
        self.assertEqual(upper[start].channel, 2)
        self.assertEqual(upper[start].char, "2")
        self.assertTrue(all(cell.channel is None for cell in lower))

    def test_two_channels_on_one_key_take_a_cell_each(self):
        _, lower = self.cells(state_with({0: C4, 4: C4}))
        _, start, _ = keyboard.KEYS[36]
        self.assertEqual([lower[start].channel, lower[start + 1].channel], [0, 4])
        self.assertEqual([lower[start].char, lower[start + 1].char], ["0", "4"])

    def test_more_channels_than_cells_show_the_overflow_mark(self):
        _, lower = self.cells(state_with({0: C4, 1: C4, 2: C4}))
        _, start, count = keyboard.KEYS[36]
        self.assertEqual(lower[start + count - 1].char, keyboard.MORE_MARK)

    def test_a_pitch_above_the_keyboard_marks_the_right_edge(self):
        _, lower = self.cells(state_with({3: C6}))
        self.assertEqual(lower[-1].char, keyboard.ABOVE_MARK)
        self.assertEqual(lower[-1].channel, 3)

    def test_a_pitch_below_the_keyboard_marks_the_left_edge(self):
        _, lower = self.cells(state_with({3: 0}))
        self.assertEqual(lower[0].char, keyboard.BELOW_MARK)
        self.assertEqual(lower[0].channel, 3)

    def test_the_lowest_key_on_the_board_is_not_an_edge_mark(self):
        _, lower = self.cells(state_with({0: C1}))
        self.assertEqual(lower[0].char, "0")
        self.assertEqual(lower[0].channel, 0)


if __name__ == "__main__":
    unittest.main()
