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
