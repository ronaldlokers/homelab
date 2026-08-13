#!/usr/bin/env python3
"""What the sheet claims about the data.

These are the tests that matter most here. The digest makes assertions about
health — "out of range on 12 of 14 days" — and until now nothing checked the
arithmetic behind them. A wrong chart is obvious; a wrong sentence is not.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from helpers import BANDS, expected, fixture, load  # noqa: E402

chart = load("chart")


def flat_day(value, count=288):
    return [(step * 5, value) for step in range(count)]


class Golden(unittest.TestCase):
    """The committed fortnights, and what the current code says about them.

    These files are the contract a port has to meet. Changing them is a
    deliberate act — `make_fixtures.py` rewrites them, and a diff there means
    the digest now says something different about the same data, which is
    exactly the change worth seeing in a review.
    """

    def test_fixtures_reproduce(self):
        for name in ("ordinary", "spike", "low", "flat"):
            with self.subTest(name):
                days = fixture(f"fortnight-{name}")
                golden = expected(f"fortnight-{name}")
                self.assertEqual(
                    [list(item) for item in chart.findings(days, BANDS)],
                    golden["findings"],
                )
                self.assertEqual(
                    [list(item) for item in chart.outliers(days, BANDS)],
                    golden["outliers"],
                )

    def test_each_fixture_leads_with_a_different_finding(self):
        """A fixture that headlines the same thing as another tests nothing new."""
        leads = {
            name: chart.outliers(fixture(f"fortnight-{name}"), BANDS)[0][0]
            for name in ("ordinary", "spike", "low", "flat")
        }
        self.assertEqual(len(set(leads.values())), 4, leads)


class Priority(unittest.TestCase):
    """The headline is the most consequential true thing, not the first computed."""

    def test_a_low_outranks_everything(self):
        days = fixture("fortnight-spike")
        label, readings = days[-1]
        # Drop one reading under the floor; it should take the headline from
        # the 5.3 mmol morning excursion.
        sunk = [(m, 50 if m == 600 else v) for m, v in readings]
        headline = chart.outliers(days[:-1] + [(label, sunk)], BANDS)[0]
        self.assertEqual(headline[0], "below 3.9")

    def test_weekly_low_outranks_the_worst_hour(self):
        self.assertEqual(chart.findings(fixture("fortnight-low"), BANDS)[0][0], "below 3.9")

    def test_quiet_fortnight_still_says_something(self):
        days = [(f"day {i}", flat_day(110)) for i in range(14)]
        found = chart.findings(days, BANDS)
        self.assertTrue(found)
        self.assertEqual(found[0][0], "nights")

    def test_ordinary_day_is_allowed_to_be_ordinary(self):
        """A sheet that manufactures a finding every morning trains you to ignore it."""
        head = chart.outliers(fixture("fortnight-flat"), BANDS)[0]
        self.assertEqual(head[0], "nothing unusual")


class Ranking(unittest.TestCase):
    """Where the day placed among its neighbours."""

    def test_identical_days_are_not_the_worst_day(self):
        """The bug the flat fixture found: 100% in range called 'the lowest'."""
        days = [(f"day {i}", flat_day(110)) for i in range(14)]
        text = dict(chart.outliers(days, BANDS))
        self.assertNotIn("hardest lately", text)
        self.assertIn("the same as every day", text["in range"])

    def test_best_day_is_named(self):
        days = [(f"day {i}", flat_day(200)) for i in range(13)]
        days.append(("today", flat_day(110)))
        self.assertIn("best in weeks", dict(chart.outliers(days, BANDS)))

    def test_worst_day_is_named(self):
        days = [(f"day {i}", flat_day(110)) for i in range(13)]
        days.append(("today", flat_day(200)))
        self.assertIn("hardest lately", dict(chart.outliers(days, BANDS)))

    def test_too_little_history_says_so_rather_than_guessing(self):
        days = [(f"day {i}", flat_day(110)) for i in range(3)]
        self.assertEqual(chart.outliers(days, BANDS)[0][0], "first days")


class Arithmetic(unittest.TestCase):
    def test_time_in_range_counts_the_edges_as_inside(self):
        self.assertEqual(chart.time_in_range([70, 180]), 1.0)
        self.assertEqual(chart.time_in_range([69, 181]), 0.0)
        self.assertAlmostEqual(chart.time_in_range([69, 100, 100, 181]), 0.5)

    def test_hours_by_band_takes_the_majority_of_each_hour(self):
        readings = [(m, 200 if m < 40 else 110) for m in range(0, 60, 5)]
        readings += [(m, 110) for m in range(60, 1440, 5)]
        hours = chart.hours_by_band(readings, BANDS)
        self.assertEqual(hours[0], "high")
        self.assertEqual(hours[1], "in range")

    def test_an_hour_with_no_readings_is_none_not_zero(self):
        readings = [(m, 110) for m in range(0, 1440, 5) if not 180 <= m < 240]
        self.assertIsNone(chart.hours_by_band(readings, BANDS)[3])

    def test_modal_day_is_the_median_and_the_middle_half(self):
        days = [(f"day {i}", flat_day(100 + i * 10)) for i in range(5)]
        band = chart.modal_day(days, slots=24)
        self.assertTrue(band)
        for _, low, median, high in band:
            self.assertEqual((low, median, high), (110, 120, 130))

    def test_bands_are_inclusive_at_both_ends(self):
        for value, name in ((53, "very low"), (54, "low"), (70, "in range"), (180, "in range"), (251, "very high")):
            with self.subTest(value):
                self.assertEqual(chart.band_of(value, BANDS), name)


class Voice(unittest.TestCase):
    """The findings describe; they never instruct.

    Not a style rule. Advice about insulin or food from a chat bot is a medical
    device wearing a chat message, and the line is easy to cross by accident
    when someone adds a helpful-sounding finding later.
    """

    FORBIDDEN = (
        "you should", "try ", "consider ", "increase", "decrease", "reduce",
        "take ", "adjust", "correct ", "dose", "insulin", "carb", "eat ",
    )

    def test_no_finding_tells_the_reader_what_to_do(self):
        for name in ("ordinary", "spike", "low", "flat"):
            days = fixture(f"fortnight-{name}")
            for key, text in chart.findings(days, BANDS) + chart.outliers(days, BANDS):
                sentence = f"{key} {text}".lower()
                for phrase in self.FORBIDDEN:
                    with self.subTest(name=name, phrase=phrase):
                        self.assertNotIn(phrase, sentence)


if __name__ == "__main__":
    unittest.main()
