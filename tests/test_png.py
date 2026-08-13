#!/usr/bin/env python3
"""That the sheets are well-formed PNGs, and that nothing runs off them.

The encoder is hand-written — signature, chunks, CRCs and per-row filters are
all ours — so "the file opens" is a real thing to assert rather than something
the library guarantees.
"""

import pathlib
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from helpers import BANDS, fixture, load  # noqa: E402

chart = load("chart")


def chunks(png):
    """Walk the file the way a decoder would, checking every CRC on the way."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "bad signature"
    offset, seen = 8, []
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        tag = png[offset + 4 : offset + 8]
        body = png[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, f"bad CRC on {tag}"
        seen.append((tag, body))
        offset += 12 + length
    return seen


class Structure(unittest.TestCase):
    SHEETS = ("render_fortnight", "render_day")

    def test_every_sheet_decodes(self):
        for name in ("ordinary", "spike", "low", "flat"):
            for sheet in self.SHEETS:
                with self.subTest(fixture=name, sheet=sheet):
                    png = getattr(chart, sheet)(fixture(f"fortnight-{name}"), BANDS)
                    tags = [tag for tag, _ in chunks(png)]
                    self.assertEqual(tags, [b"IHDR", b"IDAT", b"IEND"])

    def test_the_canvas_is_the_box_campfire_gives_it(self):
        png = chart.render_day(fixture("fortnight-ordinary"), BANDS)
        header = dict(chunks(png))[b"IHDR"]
        width, height, depth, colour = struct.unpack(">IIBB", header[:10])
        self.assertEqual((width, height), (1000, 1200))
        self.assertEqual((depth, colour), (8, 2))  # 8-bit truecolour

    def test_the_pixels_decompress_to_the_right_length(self):
        png = chart.render_fortnight(fixture("fortnight-ordinary"), BANDS)
        body = dict(chunks(png))
        width, height = struct.unpack(">II", body[b"IHDR"][:8])
        raw = zlib.decompress(body[b"IDAT"])
        # One filter byte per row, then three bytes a pixel.
        self.assertEqual(len(raw), height * (1 + width * 3))

    def test_every_row_declares_a_filter_the_spec_allows(self):
        png = chart.render_day(fixture("fortnight-low"), BANDS)
        body = dict(chunks(png))
        width, height = struct.unpack(">II", body[b"IHDR"][:8])
        raw = zlib.decompress(body[b"IDAT"])
        stride = 1 + width * 3
        for row in range(height):
            with self.subTest(row=row):
                self.assertIn(raw[row * stride], (0, 1, 2, 3, 4))


class Robustness(unittest.TestCase):
    def test_a_short_history_still_draws(self):
        """Three days is below what the outliers can compare, but not undrawable."""
        days = fixture("fortnight-ordinary")[-3:]
        for sheet in ("render_fortnight", "render_day"):
            with self.subTest(sheet):
                png = getattr(chart, sheet)(days, BANDS)
                self.assertEqual([t for t, _ in chunks(png)][0], b"IHDR")

    def test_a_single_day_does_not_crash_the_daily_sheet(self):
        days = fixture("fortnight-ordinary")[-1:]
        png = chart.render_day(days, BANDS)
        self.assertEqual([t for t, _ in chunks(png)][-1], b"IEND")

    def test_a_day_of_one_reading_is_survivable(self):
        days = fixture("fortnight-ordinary")[:-1] + [("odd", [(720, 110)])]
        png = chart.render_day(days, BANDS)
        self.assertEqual([t for t, _ in chunks(png)][-1], b"IEND")

    def test_findings_never_grow_into_the_foot(self):
        """The collision this test exists for was shipped and running.

        Whenever the first finding wrapped to two lines the second was drawn
        across the rule and the figures beneath it. Findings arrive in order of
        consequence, so one that does not fit is dropped rather than drawn over
        the numbers.
        """
        type_ = chart.Type()
        long_ones = [
            ("04:00", "ran 3.4 mmol below its usual, the day's biggest departure"),
            ("in range", "88%, better than 8 of the last 13 days"),
            ("the night", "1.2 mmol lower than your usual night"),
        ]
        for start in (chart.FINDINGS, chart.DAY_FINDINGS):
            with self.subTest(start=start):
                canvas = chart.Canvas(chart.WIDTH, chart.HEIGHT, chart.GROUND)
                bottom = chart.draw_findings(canvas, type_, long_ones, start)
                self.assertLessEqual(bottom, chart.FOOT - 24)

    def test_headline_wraps_rather_than_overflowing(self):
        """The headline is computed text, so its length is not knowable here."""
        type_ = chart.Type()
        long_one = "below 3.9 reached 2.7 at 02:55, after 13 of the last 13 days stayed clear"
        lines = type_.wrap("headline", long_one, chart.WIDTH - 2 * chart.MARGIN)
        for line in lines:
            with self.subTest(line):
                self.assertLessEqual(
                    type_.width("headline", line), chart.WIDTH - 2 * chart.MARGIN
                )


if __name__ == "__main__":
    unittest.main()
