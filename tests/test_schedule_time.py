from datetime import date, datetime, time, timezone
from unittest import TestCase

from superharry.schedule_time import next_occurrence, resolve_wall_time


class ScheduleTimeTests(TestCase):
    def test_gap_moves_to_first_valid_time(self):
        self.assertEqual(
            resolve_wall_time(date(2026, 3, 8), time(2, 30), "America/New_York"),
            datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
        )

    def test_fold_uses_first_occurrence(self):
        self.assertEqual(
            resolve_wall_time(date(2026, 11, 1), time(1, 30), "America/New_York"),
            datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        )

    def test_daily_recurrence_keeps_wall_time_across_dst(self):
        self.assertEqual(
            next_occurrence(datetime(2026, 3, 7, 15, tzinfo=timezone.utc), time(9), "America/New_York", "daily"),
            datetime(2026, 3, 8, 13, tzinfo=timezone.utc),
        )

    def test_weekly_excludes_current_instant(self):
        self.assertEqual(
            next_occurrence(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), time(1, 30), "America/New_York", "weekly", 6),
            datetime(2026, 11, 8, 6, 30, tzinfo=timezone.utc),
        )
