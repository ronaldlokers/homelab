#!/usr/bin/env python3
"""Which day is being reported, and which sheet describes it.

Every boundary here is a local midnight in Europe/Amsterdam. Getting one wrong
does not crash — it reports the wrong day, which is the worst failure this
program has, because the output still looks entirely plausible.
"""

import pathlib
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from helpers import load  # noqa: E402

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def digest(**environment):
    return load("nightscout-digest", **environment)


def entries_for(start, days, every_minutes=5, value=110):
    """Timestamps in ms, as Nightscout returns them, spanning whole local days."""
    out = []
    for offset in range(days):
        midnight = start - timedelta(days=offset)
        for minute in range(0, 1440, every_minutes):
            when = midnight + timedelta(minutes=minute)
            out.append((int(when.timestamp() * 1000), value))
    return out


class DayBounds(unittest.TestCase):
    def test_digest_date_selects_that_day(self):
        module = digest(DIGEST_DATE="2026-08-05")
        day, start, end = module.day_bounds()
        self.assertEqual(day.date().isoformat(), "2026-08-05")
        self.assertEqual((end - start) / 3_600_000, 24)

    def test_spring_forward_is_a_23_hour_day(self):
        module = digest(DIGEST_DATE="2026-03-29")
        _, start, end = module.day_bounds()
        self.assertEqual((end - start) / 3_600_000, 23)

    def test_autumn_back_is_a_25_hour_day(self):
        module = digest(DIGEST_DATE="2026-10-25")
        _, start, end = module.day_bounds()
        self.assertEqual((end - start) / 3_600_000, 25)

    def test_a_malformed_date_raises_rather_than_reporting_another_day(self):
        module = digest(DIGEST_DATE="not-a-date")
        with self.assertRaises(ValueError):
            module.day_bounds()

    def test_unset_reports_yesterday(self):
        module = digest(DIGEST_DATE="")
        day, _, _ = module.day_bounds()
        today = datetime.now(AMSTERDAM).date()
        self.assertEqual(day.date(), today - timedelta(days=1))


class SheetSelection(unittest.TestCase):
    def test_saturday_gets_the_fortnight(self):
        module = digest()
        saturday = datetime(2026, 8, 8, tzinfo=AMSTERDAM)
        self.assertTrue(module.sheet_for(saturday))

    def test_every_other_day_gets_the_daily_sheet(self):
        module = digest()
        monday = datetime(2026, 8, 10, tzinfo=AMSTERDAM)
        for offset in range(7):
            day = monday + timedelta(days=offset)
            with self.subTest(day.strftime("%A")):
                self.assertEqual(module.sheet_for(day), day.weekday() == 5)

    def test_the_override_wins_in_both_directions(self):
        saturday = datetime(2026, 8, 8, tzinfo=AMSTERDAM)
        wednesday = datetime(2026, 8, 12, tzinfo=AMSTERDAM)
        self.assertFalse(digest(DIGEST_SHEET="day").sheet_for(saturday))
        self.assertTrue(digest(DIGEST_SHEET="fortnight").sheet_for(wednesday))

    def test_an_unrecognised_override_falls_back_to_the_calendar(self):
        module = digest(DIGEST_SHEET="weekly")
        self.assertTrue(module.sheet_for(datetime(2026, 8, 8, tzinfo=AMSTERDAM)))


class WholeDay(unittest.TestCase):
    """How many readings a whole day holds, on the two days it is not 288."""

    def expected_for(self, iso):
        module = digest(DIGEST_DATE=iso)
        _, start, end = module.day_bounds()
        return module.whole_day(start, end)

    def test_an_ordinary_day_holds_288(self):
        self.assertEqual(self.expected_for("2026-08-05"), 288)

    def test_the_short_day_holds_276(self):
        self.assertEqual(self.expected_for("2026-03-29"), 276)

    def test_the_long_day_holds_300(self):
        self.assertEqual(self.expected_for("2026-10-25"), 300)

    def test_a_perfect_sensor_is_100_percent_on_every_day(self):
        """The bug: a fixed 288 read a full sensor as 104% in October.

        Nothing downstream broke, because the only consumer is a floor. But a
        coverage figure over 100 is a figure that is wrong, and it was printed
        on the days the statistics were withheld.
        """
        for iso in ("2026-08-05", "2026-03-29", "2026-10-25"):
            with self.subTest(iso):
                module = digest(DIGEST_DATE=iso)
                _, start, end = module.day_bounds()
                whole = module.whole_day(start, end)
                readings = int((end - start) / 1000 // 300)
                self.assertEqual(round(readings / whole * 100), 100)


class SplitIntoDays(unittest.TestCase):
    def test_days_come_back_oldest_first(self):
        module = digest()
        start = datetime(2026, 8, 5, tzinfo=AMSTERDAM)
        days = module.split_into_days(entries_for(start, 14), start)
        self.assertEqual(len(days), 14)
        self.assertEqual(days[0][0], "23 Jul")
        self.assertEqual(days[-1][0], "5 Aug")

    def test_minutes_are_offsets_within_the_local_day(self):
        module = digest()
        start = datetime(2026, 8, 5, tzinfo=AMSTERDAM)
        days = module.split_into_days(entries_for(start, 1), start)
        minutes = [minute for minute, _ in days[-1][1]]
        self.assertEqual(minutes[0], 0)
        self.assertEqual(minutes[-1], 1435)

    def test_a_sparse_day_is_dropped_rather_than_drawn_empty(self):
        """A blank row reads as a day at zero, which is a worse claim."""
        module = digest()
        start = datetime(2026, 8, 5, tzinfo=AMSTERDAM)
        entries = entries_for(start, 14)
        cutoff = int((start - timedelta(days=3)).timestamp() * 1000)
        kept = [
            (stamp, value)
            for stamp, value in entries
            if not (cutoff <= stamp < cutoff + 3_600_000 * 24)
            or stamp < cutoff + 600_000
        ]
        days = module.split_into_days(kept, start)
        self.assertEqual(len(days), 13)

    def test_minutes_are_wall_clock_so_hours_line_up_across_days(self):
        """The subtlety worth a test: hours must mean the same thing every day.

        `(when - midnight)` reads like elapsed time and is not — Python ignores
        the offset when both datetimes share a tzinfo. On the day the clocks go
        back that is the difference between a reading at 23:55 landing on
        minute 1435 and on minute 1495, and a minute past 1439 falls off both
        the counted row and the right-hand edge of the plot.

        It also decides whether "the 08:00 hour" means 08:00 on every day or
        drifts by an hour after each changeover.
        """
        module = digest()
        start = datetime(2026, 10, 25, tzinfo=AMSTERDAM)
        first = start.timestamp()
        # A real sensor: one reading every five minutes of actual elapsed time,
        # so a 25-hour day produces 300 of them rather than 288.
        entries = [(int((first + i * 300) * 1000), 110) for i in range(300)]

        days = module.split_into_days(entries, start, count=1)
        minutes = [minute for minute, _ in days[0][1]]
        self.assertEqual(len(minutes), 300, "no reading may be dropped")
        self.assertLess(max(minutes), 1440, "a minute past 1439 falls off the sheet")
        # The hour that happened twice lands in the same slot twice, which is
        # the honest answer: it did happen twice.
        self.assertEqual(len(minutes) - len(set(minutes)), 12)

    def test_the_spring_forward_day_is_short_but_kept(self):
        module = digest()
        start = datetime(2026, 3, 29, tzinfo=AMSTERDAM)
        days = module.split_into_days(entries_for(start, 3), start)
        labels = [label for label, _ in days]
        self.assertIn("29 Mar", labels)
        minutes = dict(days)["29 Mar"]
        # 23 local hours of readings, and none of them past the end of the day.
        self.assertLessEqual(max(m for m, _ in minutes), 1440)


if __name__ == "__main__":
    unittest.main()
