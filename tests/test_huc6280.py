import unittest

from pcevgm.huc6280 import DEFAULT_CLOCK, HuC6280State, note_text


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


if __name__ == "__main__":
    unittest.main()
