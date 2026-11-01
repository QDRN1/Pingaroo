import unittest

from PIL import Image

from pingaroo.app import apply_brightness, brightness_level, in_night_window
from pingaroo.config import DEFAULTS, parse_hhmm


DISP = dict(DEFAULTS["display"])


class NightWindow(unittest.TestCase):
    def test_wraps_midnight(self):
        s, e = parse_hhmm("20:00"), parse_hhmm("06:00")
        self.assertTrue(in_night_window(parse_hhmm("21:34"), s, e))
        self.assertTrue(in_night_window(parse_hhmm("00:00"), s, e))
        self.assertTrue(in_night_window(parse_hhmm("05:59"), s, e))
        self.assertFalse(in_night_window(parse_hhmm("06:00"), s, e))
        self.assertFalse(in_night_window(parse_hhmm("12:00"), s, e))
        self.assertFalse(in_night_window(parse_hhmm("19:59"), s, e))

    def test_same_day_window_and_degenerate(self):
        self.assertTrue(in_night_window(600, 540, 720))
        self.assertFalse(in_night_window(720, 540, 720))
        self.assertFalse(in_night_window(600, 600, 600))

    def test_parse(self):
        self.assertEqual(parse_hhmm("08:05"), 485)
        self.assertIsNone(parse_hhmm("24:00"))
        self.assertIsNone(parse_hhmm("eight"))
        self.assertIsNone(parse_hhmm(None))


class Levels(unittest.TestCase):
    def test_day_active_full(self):
        self.assertEqual(brightness_level(0, parse_hhmm("12:00"), DISP), 1.0)

    def test_day_idle_quarter(self):
        self.assertEqual(brightness_level(60, parse_hhmm("12:00"), DISP), 0.25)
        self.assertEqual(brightness_level(59, parse_hhmm("12:00"), DISP), 1.0)

    def test_night_active_quarter_idle_five(self):
        self.assertEqual(brightness_level(0, parse_hhmm("21:34"), DISP), 0.25)
        self.assertEqual(brightness_level(60, parse_hhmm("21:34"), DISP), 0.05)

    def test_dim_disabled(self):
        d = dict(DISP, dim_after_seconds=0)
        self.assertEqual(brightness_level(9999, parse_hhmm("12:00"), d), 1.0)
        self.assertEqual(brightness_level(9999, parse_hhmm("23:00"), d), 0.25)


class Apply(unittest.TestCase):
    def test_scales_pixels(self):
        img = Image.new("RGB", (4, 4), (200, 100, 0))
        self.assertEqual(apply_brightness(img, 0.5).getpixel((0, 0)), (100, 50, 0))
        self.assertEqual(apply_brightness(img, 0.25).getpixel((0, 0)), (50, 25, 0))
        self.assertEqual(apply_brightness(img, 0.05).getpixel((0, 0)), (10, 5, 0))
        self.assertIs(apply_brightness(img, 1.0), img)


if __name__ == "__main__":
    unittest.main()
