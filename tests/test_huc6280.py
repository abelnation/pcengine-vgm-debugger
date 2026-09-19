import unittest

from pcevgm.huc6280 import (
    BLOCKS,
    DEFAULT_CLOCK,
    HBLOCKS,
    MAX_VOLUME_STEPS,
    METER_FLOOR_DB,
    PLOT_LEVELS,
    SCAN_LINES,
    SAMPLE_MASK,
    WAVE_LENGTH,
    WAVE_ROWS,
    LFO_DEPTH_FACTORS,
    HuC6280State,
    db_fraction,
    describe_write,
    lfo_depth_factor,
    meter,
    note_text,
    sparkline,
    wave_rows,
)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.state = HuC6280State(DEFAULT_CLOCK)

    def test_frequency_uses_both_halves(self):
        self.state.write(0x00, 2)
        self.state.write(0x02, 0x34)
        self.state.write(0x03, 0x12)
        self.assertEqual(self.state.channels[2].frequency, 0x234)

    def test_freq_high_ignores_upper_nibble(self):
        self.state.write(0x00, 0)
        self.state.write(0x03, 0xF7)
        self.assertEqual(self.state.channels[0].frequency, 0x700)

    def test_control_splits_enable_dda_and_amplitude(self):
        self.state.write(0x00, 1)
        self.state.write(0x04, 0xC5)
        channel = self.state.channels[1]
        self.assertTrue(channel.enabled)
        self.assertTrue(channel.dda)
        self.assertEqual(channel.amplitude, 5)

    def test_wave_writes_advance_and_wrap(self):
        self.state.write(0x00, 0)
        for value in range(33):
            self.state.write(0x06, value)
        channel = self.state.channels[0]
        self.assertEqual(channel.waveform[0], 32 & 0x1F)  # the 33rd write wrapped
        self.assertEqual(channel.waveform[1], 1)
        self.assertEqual(channel.wave_index, 1)

    def test_disabling_resets_the_wave_pointer(self):
        self.state.write(0x00, 0)
        self.state.write(0x06, 7)
        self.state.write(0x04, 0x00)
        self.assertEqual(self.state.channels[0].wave_index, 0)

    def test_dda_write_does_not_touch_the_wave_table(self):
        self.state.write(0x00, 0)
        self.state.write(0x04, 0xC0)  # enabled, DDA
        self.state.write(0x06, 9)
        channel = self.state.channels[0]
        self.assertEqual(channel.dda_sample, 9)
        self.assertEqual(channel.waveform[0], 0)

    def test_noise_only_on_channels_4_and_5(self):
        self.state.write(0x00, 3)
        self.state.write(0x07, 0x85)
        self.assertFalse(self.state.channels[3].noise_enabled)
        self.state.write(0x00, 4)
        self.state.write(0x07, 0x85)
        self.assertTrue(self.state.channels[4].noise_enabled)
        self.assertEqual(self.state.channels[4].noise_frequency, 5)

    def test_channel_6_and_7_writes_are_dropped(self):
        self.state.write(0x00, 7)
        self.state.write(0x04, 0x9F)  # must not raise
        self.assertEqual(self.state.selected, 7)

    def test_master_balance_splits_nibbles(self):
        self.state.write(0x01, 0xA3)
        self.assertEqual((self.state.master_left, self.state.master_right), (0xA, 0x3))


class FrequencyTests(unittest.TestCase):
    def test_divider_maps_to_a440(self):
        state = HuC6280State(DEFAULT_CLOCK)
        state.write(0x00, 0)
        divider = round(DEFAULT_CLOCK / (32 * 440))
        state.write(0x02, divider & 0xFF)
        state.write(0x03, divider >> 8)
        hz = state.channels[0].frequency_hz(state.clock)
        self.assertAlmostEqual(hz, 440.0, delta=1.0)
        self.assertTrue(note_text(hz).startswith("A4"))

    def test_zero_divider_means_4096(self):
        state = HuC6280State(DEFAULT_CLOCK)
        hz = state.channels[0].frequency_hz(state.clock)
        self.assertAlmostEqual(hz, DEFAULT_CLOCK / (32 * 4096), places=4)


class LevelTests(unittest.TestCase):
    def test_disabled_channel_has_no_level(self):
        state = HuC6280State()
        self.assertIsNone(state.channels[0].levels_db(0x0F, 0x0F))

    def test_full_volume_is_zero_db(self):
        state = HuC6280State()
        state.write(0x00, 0)
        state.write(0x04, 0x9F)  # on, amplitude 31
        state.write(0x05, 0xFF)  # balance full both sides
        left, right = state.channels[0].levels_db(0x0F, 0x0F)
        self.assertAlmostEqual(left, 0.0)
        self.assertAlmostEqual(right, 0.0)


def plot_level(value):
    """Read a one-sample plot back as a level, 0 at the bottom."""
    rows = wave_rows([value])
    marked = [(row, line[0]) for row, line in enumerate(rows) if line[0] != " "]
    assert len(marked) == 1, marked
    row, glyph = marked[0]
    within = len(SCAN_LINES) - 1 - SCAN_LINES.index(glyph)
    return PLOT_LEVELS - 1 - (row * len(SCAN_LINES) + within)


class WavePlotTests(unittest.TestCase):
    def test_shape_is_rows_by_wave_length(self):
        rows = wave_rows([0] * WAVE_LENGTH)
        self.assertEqual(len(rows), WAVE_ROWS)
        for row in rows:
            self.assertEqual(len(row), WAVE_LENGTH)

    def test_a_line_leaves_one_mark_per_column(self):
        rows = wave_rows(list(range(WAVE_LENGTH)))
        for column in range(WAVE_LENGTH):
            marks = [row[column] for row in rows if row[column] != " "]
            self.assertEqual(len(marks), 1, column)

    def test_the_lowest_sample_sits_at_the_bottom(self):
        top, bottom = wave_rows([0])
        self.assertEqual(top, " ")
        self.assertEqual(bottom, SCAN_LINES[0])
        self.assertEqual(plot_level(0), 0)

    def test_the_highest_sample_sits_at_the_top(self):
        top, bottom = wave_rows([SAMPLE_MASK])
        self.assertEqual(top, SCAN_LINES[-1])
        self.assertEqual(bottom, " ")
        self.assertEqual(plot_level(SAMPLE_MASK), PLOT_LEVELS - 1)

    def test_every_level_is_reachable(self):
        levels = {plot_level(value) for value in range(SAMPLE_MASK + 1)}
        self.assertEqual(levels, set(range(PLOT_LEVELS)))

    def test_the_level_never_drops_as_the_sample_rises(self):
        levels = [plot_level(value) for value in range(SAMPLE_MASK + 1)]
        self.assertEqual(levels, sorted(levels))

    def test_the_sparkline_still_fills(self):
        self.assertEqual(
            len({sparkline([v])[0] for v in range(SAMPLE_MASK + 1)}), len(BLOCKS)
        )
        self.assertEqual(sparkline([SAMPLE_MASK]), BLOCKS[-1])


class MeterTests(unittest.TestCase):
    def test_width_is_fixed(self):
        for fraction in (0.0, 0.3, 0.75, 1.0):
            self.assertEqual(len(meter(fraction, 6)), 6)

    def test_empty_and_full_ends(self):
        self.assertEqual(meter(0.0, 4), " " * 4)
        self.assertEqual(meter(1.0, 4), HBLOCKS[-1] * 4)

    def test_out_of_range_input_clamps(self):
        self.assertEqual(meter(-5.0, 4), " " * 4)
        self.assertEqual(meter(5.0, 4), HBLOCKS[-1] * 4)

    def test_a_partial_block_shows_below_one_character(self):
        self.assertEqual(meter(0.5, 1), HBLOCKS[3])

    def test_fill_never_drops_as_the_fraction_rises(self):
        def fill(fraction):
            bar = meter(fraction, 6).rstrip()
            return len(bar) * len(HBLOCKS) if not bar else (
                (len(bar) - 1) * len(HBLOCKS) + HBLOCKS.index(bar[-1]) + 1
            )

        filled = [fill(step / 60) for step in range(61)]
        self.assertEqual(filled, sorted(filled))


class LevelMeterScaleTests(unittest.TestCase):
    def test_full_volume_fills_and_the_floor_empties(self):
        self.assertEqual(db_fraction(0.0), 1.0)
        self.assertEqual(db_fraction(METER_FLOOR_DB), 0.0)
        self.assertEqual(db_fraction(METER_FLOOR_DB - 20), 0.0)

    def test_half_way_to_the_floor_reads_half(self):
        self.assertAlmostEqual(db_fraction(METER_FLOOR_DB / 2), 0.5)

    def test_quiet_and_loud_stay_apart(self):
        self.assertGreater(db_fraction(-7.5) - db_fraction(-49.5), 0.5)


class LevelStepTests(unittest.TestCase):
    def test_off_channel_has_no_steps(self):
        self.assertIsNone(HuC6280State().channels[0].level_steps(0x0F, 0x0F))

    def test_steps_run_from_zero_to_the_maximum(self):
        state = HuC6280State()
        state.write(0x00, 0)
        state.write(0x04, 0x80)  # on, amplitude 0
        self.assertEqual(state.channels[0].level_steps(0, 0), (0, 0))
        state.write(0x04, 0x9F)  # on, amplitude 31
        state.write(0x05, 0xFF)
        self.assertEqual(
            state.channels[0].level_steps(0x0F, 0x0F),
            (MAX_VOLUME_STEPS, MAX_VOLUME_STEPS),
        )


class LfoControlTests(unittest.TestCase):
    def setUp(self):
        self.state = HuC6280State()

    def test_reset_state_is_enabled_at_zero_depth(self):
        self.assertTrue(self.state.lfo_enabled)
        self.assertEqual(self.state.lfo_depth, 0)

    def test_bit_7_disables_and_the_low_bits_set_depth(self):
        self.state.write(0x09, 0x82)
        self.assertFalse(self.state.lfo_enabled)
        self.assertEqual(self.state.lfo_depth, 2)
        self.assertEqual(self.state.lfo_control, 0x82)

    def test_clearing_bit_7_enables_again(self):
        self.state.write(0x09, 0x83)
        self.state.write(0x09, 0x03)
        self.assertTrue(self.state.lfo_enabled)
        self.assertEqual(self.state.lfo_depth, 3)

    def test_middle_bits_are_not_depth(self):
        self.state.write(0x09, 0x7C)  # every bit between 7 and 2
        self.assertTrue(self.state.lfo_enabled)
        self.assertEqual(self.state.lfo_depth, 0)

    def test_the_frequency_register_leaves_depth_alone(self):
        self.state.write(0x09, 0x03)
        self.state.write(0x08, 0xFF)
        self.assertEqual(self.state.lfo_frequency, 0xFF)
        self.assertEqual(self.state.lfo_depth, 3)

    def test_depth_codes_map_to_their_factors(self):
        self.assertEqual(
            [lfo_depth_factor(code) for code in range(4)], list(LFO_DEPTH_FACTORS)
        )
        self.assertEqual(lfo_depth_factor(0xFE), LFO_DEPTH_FACTORS[2])

    def test_written_text_names_both_fields(self):
        self.assertEqual(describe_write(0x09, 0x02, 0), "LFO on  depth 2 (x16)")
        self.assertEqual(describe_write(0x09, 0x83, 0), "LFO off depth 3 (x256)")


if __name__ == "__main__":
    unittest.main()
