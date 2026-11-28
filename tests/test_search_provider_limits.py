"""Deterministic tests for Mwmbl limits within the host skill-worker runtime."""

import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.worker_lock import lock_spool
from superharry.search_provider_limits import (
    MwmblLimiterClockError,
    MwmblMonthlyQuotaExceeded,
    MwmblRequestLimiter,
    MwmblLimiterStateError,
    configured_mwmbl_limiter,
)


class FakeClock:
    def __init__(self, timestamp=None):
        self.wall = time.time() if timestamp is None else timestamp
        self.elapsed = 0.0
        self.lock = threading.Lock()
        self.sleeps = []

    def now(self):
        with self.lock:
            return self.wall

    def monotonic(self):
        with self.lock:
            return self.elapsed

    def sleep(self, delay):
        with self.lock:
            self.sleeps.append(delay)
            self.wall += delay
            self.elapsed += delay


def crash_during_dispatch(spool):
    limiter = MwmblRequestLimiter(spool)
    with lock_spool(Path(spool)):
        limiter.dispatch(lambda: os._exit(23))


def hold_spool_worker(spool, acquired, release, outcomes):
    try:
        with lock_spool(Path(spool)):
            acquired.set()
            release.wait(10)
            outcomes.put("acquired")
    except OSError:
        outcomes.put("busy")


class MwmblLimiterTests(unittest.TestCase):
    def test_configured_factory_fails_closed_without_an_absolute_shared_ledger(self):
        for environment in ({}, {"MWMBL_SHARED_LEDGER_DIRECTORY": "relative-ledger"}):
            with self.subTest(environment=environment), self.assertRaises(MwmblLimiterStateError):
                configured_mwmbl_limiter(environ=environment)

    def test_shared_ledger_coordinates_distinct_worker_spools(self):
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / "egress-ledger"
            worker_one_spool = Path(root) / "worker-one-spool"
            worker_two_spool = Path(root) / "worker-two-spool"
            first = configured_mwmbl_limiter(
                environ={"MWMBL_SHARED_LEDGER_DIRECTORY": str(shared)}, monthly_limit=1,
            )
            second = configured_mwmbl_limiter(
                environ={"MWMBL_SHARED_LEDGER_DIRECTORY": str(shared)}, monthly_limit=1,
            )
            self.assertEqual(first.database_path, second.database_path)
            self.assertNotEqual(worker_one_spool, worker_two_spool)

            self.assertEqual(first.dispatch(lambda: "first"), "first")
            with self.assertRaises(MwmblMonthlyQuotaExceeded):
                second.dispatch(lambda: self.fail("shared monthly quota must block this dispatch"))

    def test_utc_month_rollover_quota_boundary_and_clock_regression(self):
        clock = FakeClock(datetime(2026, 11, 30, 23, 59, 50, tzinfo=timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as directory:
            limiter = MwmblRequestLimiter(
                directory, monthly_limit=2, wall_clock=clock.now, sleeper=clock.sleep,
                monotonic=clock.monotonic,
            )
            limiter.dispatch(lambda: "one")
            limiter.dispatch(lambda: "two")
            with self.assertRaises(MwmblMonthlyQuotaExceeded):
                limiter.dispatch(lambda: self.fail("quota must block before dispatch"))
            self.assertEqual(self._usage(limiter), ("2026-11", 2, 0))

            clock.wall = datetime(2026, 12, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
            self.assertEqual(limiter.dispatch(lambda: "new month"), "new month")
            self.assertEqual(self._usage(limiter), ("2026-12", 1, 0))

            clock.wall = datetime(2026, 11, 30, tzinfo=timezone.utc).timestamp()
            with self.assertRaises(MwmblLimiterClockError):
                limiter.dispatch(lambda: self.fail("clock regression must fail closed"))

    def test_pacing_and_usage_survive_limiter_restart(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            starts = []
            first = MwmblRequestLimiter(directory, monthly_limit=2,
                                        wall_clock=clock.now, monotonic=clock.monotonic,
                                        sleeper=clock.sleep)
            first.dispatch(lambda: starts.append(clock.now()))
            restarted = MwmblRequestLimiter(directory, monthly_limit=2,
                                            wall_clock=clock.now, monotonic=clock.monotonic,
                                            sleeper=clock.sleep)
            restarted.dispatch(lambda: starts.append(clock.now()))
            self.assertEqual(len(starts), 2)
            self.assertGreaterEqual(starts[1] - starts[0], 1.0)
            self.assertEqual(self._usage(restarted), (self._month(clock.now()), 2, 0))

            exhausted = MwmblRequestLimiter(directory, monthly_limit=2,
                                            wall_clock=clock.now, monotonic=clock.monotonic,
                                            sleeper=clock.sleep)
            with self.assertRaises(MwmblMonthlyQuotaExceeded):
                exhausted.dispatch(lambda: self.fail("restart must retain quota usage"))

    def test_provider_failure_consumes_attempt_without_retry(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            limiter = MwmblRequestLimiter(directory, wall_clock=clock.now,
                                          monotonic=clock.monotonic, sleeper=clock.sleep)
            calls = []

            def fail():
                calls.append("called")
                raise TimeoutError("fixture failure")

            with self.assertRaisesRegex(TimeoutError, "fixture failure"):
                limiter.dispatch(fail)
            self.assertEqual(calls, ["called"])
            self.assertEqual(self._usage(limiter), (self._month(clock.now()), 1, 0))

    def test_concurrent_threads_share_one_ledger_and_one_second_spacing(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            limiters = [
                MwmblRequestLimiter(directory, wall_clock=clock.now,
                                    monotonic=clock.monotonic, sleeper=clock.sleep)
                for _ in range(2)
            ]
            starts = []
            barrier = threading.Barrier(3)

            def caller(limiter):
                barrier.wait()
                limiter.dispatch(lambda: starts.append(clock.now()))

            threads = [threading.Thread(target=caller, args=(limiter,)) for limiter in limiters]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(starts), 2)
            self.assertGreaterEqual(abs(starts[1] - starts[0]), 1.0)
            self.assertEqual(self._usage(limiters[0]), (self._month(clock.now()), 2, 0))

    def test_crash_after_reservation_releases_worker_lock_and_charges_attempt(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            child = context.Process(target=crash_during_dispatch, args=(directory,))
            child.start()
            child.join(10)
            self.assertEqual(child.exitcode, 23)

            clock = FakeClock()
            restarted = MwmblRequestLimiter(
                directory, monthly_limit=2, wall_clock=clock.now, sleeper=clock.sleep,
                monotonic=clock.monotonic,
            )
            self.assertEqual(restarted.dispatch(lambda: "recovered"), "recovered")
            self.assertIn(1.0, clock.sleeps)
            self.assertEqual(self._usage(restarted), (self._month(clock.now()), 2, 0))

    def test_existing_spool_worker_lock_excludes_a_second_worker_process(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            acquired_first, release_first = context.Event(), context.Event()
            acquired_second, unused_release = context.Event(), context.Event()
            outcomes = context.Queue()
            first = context.Process(
                target=hold_spool_worker,
                args=(directory, acquired_first, release_first, outcomes),
            )
            first.start()
            self.assertTrue(acquired_first.wait(10))
            second = context.Process(
                target=hold_spool_worker,
                args=(directory, acquired_second, unused_release, outcomes),
            )
            second.start()
            second.join(10)
            self.assertFalse(second.is_alive())
            self.assertEqual(second.exitcode, 0)
            self.assertFalse(acquired_second.is_set())
            self.assertEqual(outcomes.get(timeout=2), "busy")
            release_first.set()
            first.join(10)
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(outcomes.get(timeout=2), "acquired")

    @staticmethod
    def _usage(limiter):
        connection = sqlite3.connect(limiter.database_path)
        try:
            row = connection.execute(
                "SELECT month, attempts, in_flight FROM mwmbl_usage WHERE singleton=1"
            ).fetchone()
        finally:
            connection.close()
        return row

    @staticmethod
    def _month(timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")


if __name__ == "__main__":
    unittest.main()
