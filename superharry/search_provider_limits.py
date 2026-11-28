"""Durable Mwmbl limits for calls dispatched by the single host skill worker.

The database coordinates callers inside that host runtime. Production callers
must run under ``scripts.run_skill_jobs.supervise``'s ``lock_spool`` scope; the
Windows-host/Linux-Docker Desktop mount does not share SQLite file locks.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class MwmblLimiterError(RuntimeError):
    """The limiter cannot safely authorize a provider dispatch."""


class MwmblMonthlyQuotaExceeded(MwmblLimiterError):
    """The shared UTC-month request budget has been exhausted."""


class MwmblLimiterStateError(MwmblLimiterError):
    """The durable limiter state is invalid or unavailable."""


class MwmblLimiterClockError(MwmblLimiterError):
    """The system UTC clock moved backwards relative to persisted state."""


_MAX_MONTHLY_ATTEMPTS = 1000
_MIN_INTERVAL_SECONDS = 1.0
SHARED_LEDGER_DIRECTORY_ENV = "MWMBL_SHARED_LEDGER_DIRECTORY"
_thread_locks_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}


def _thread_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class MwmblRequestLimiter:
    """Reserve a quota attempt and pace Mwmbl calls in one host worker runtime.

    Monthly usage is committed before the injected callback starts, so a crash
    or provider failure still consumes an attempt. This ledger is not a cross-OS
    lock service; the existing host worker spool lock is a required boundary.
    """

    def __init__(
        self,
        spool_directory: str | Path,
        *,
        monthly_limit: int = _MAX_MONTHLY_ATTEMPTS,
        lock_timeout: float = 60.0,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(monthly_limit) is not int or not 1 <= monthly_limit <= _MAX_MONTHLY_ATTEMPTS:
            raise ValueError("monthly_limit must be between 1 and 1000")
        if lock_timeout <= 0:
            raise ValueError("lock_timeout must be positive")
        self.directory = Path(spool_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.directory / "mwmbl-usage.sqlite3"
        self.monthly_limit = monthly_limit
        self.lock_timeout = lock_timeout
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.sleeper = sleeper

    def dispatch(self, callback: Callable[[], object]):
        """Persist one attempt, then call the injected transport exactly once."""
        if not callable(callback):
            raise TypeError("dispatch callback must be callable")
        thread_lock = _thread_lock_for(self.database_path)
        if not thread_lock.acquire(timeout=self.lock_timeout):
            raise MwmblLimiterStateError("Timed out waiting for the Mwmbl thread lock.")
        try:
            connection = self._connect()
            try:
                self._reserve(connection)
                dispatch_started = self.monotonic()
                try:
                    return callback()
                finally:
                    elapsed = max(0.0, self.monotonic() - dispatch_started)
                    if elapsed < _MIN_INTERVAL_SECONDS:
                        self.sleeper(_MIN_INTERVAL_SECONDS - elapsed)
                    self._finish(connection)
            finally:
                connection.close()
        finally:
            thread_lock.release()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.database_path, timeout=self.lock_timeout, isolation_level=None,
            )
            connection.execute(f"PRAGMA busy_timeout = {int(self.lock_timeout * 1000)}")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mwmbl_usage ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                "month TEXT NOT NULL, attempts INTEGER NOT NULL CHECK(attempts >= 0), "
                "last_dispatch REAL, next_allowed REAL, in_flight INTEGER NOT NULL "
                "CHECK(in_flight IN (0, 1)))"
            )
            return connection
        except (OSError, sqlite3.Error) as error:
            raise MwmblLimiterStateError("Unable to open Mwmbl quota state.") from error

    def _reserve(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = self.wall_clock()
            month = _month_at(now)
            row = connection.execute(
                "SELECT month, attempts, last_dispatch, next_allowed, in_flight "
                "FROM mwmbl_usage WHERE singleton = 1"
            ).fetchone()
            if row is None:
                stored_month, attempts, last_dispatch, next_allowed, in_flight = (
                    month, 0, None, None, 0,
                )
            else:
                stored_month, attempts, last_dispatch, next_allowed, in_flight = row
                if month < stored_month:
                    raise MwmblLimiterClockError(
                        "UTC clock month is earlier than persisted Mwmbl usage; dispatch blocked."
                    )
                if last_dispatch is not None and now < last_dispatch:
                    raise MwmblLimiterClockError(
                        "UTC clock moved backwards since the last Mwmbl dispatch; dispatch blocked."
                    )
                if month > stored_month:
                    attempts = 0
                    stored_month = month

            # A committed in-flight marker survives a process crash. Waiting a
            # full interval from recovery is conservative because its send time
            # may not be known precisely.
            if in_flight:
                self.sleeper(_MIN_INTERVAL_SECONDS)
                now = self.wall_clock()
                month = _month_at(now)
                if month < stored_month:
                    raise MwmblLimiterClockError("UTC clock moved backwards; dispatch blocked.")
                if month > stored_month:
                    attempts = 0
                    stored_month = month
                in_flight = 0

            if next_allowed is not None:
                while now < next_allowed:
                    self.sleeper(next_allowed - now)
                    now = self.wall_clock()
                    if now < (last_dispatch or now):
                        raise MwmblLimiterClockError(
                            "UTC clock moved backwards while pacing Mwmbl; dispatch blocked."
                        )
                    month = _month_at(now)
                    if month < stored_month:
                        raise MwmblLimiterClockError("UTC clock moved backwards; dispatch blocked.")
                    if month > stored_month:
                        attempts = 0
                        stored_month = month

            if attempts >= self.monthly_limit:
                raise MwmblMonthlyQuotaExceeded("Mwmbl monthly request quota has been reached.")

            # Reserve the next pacing slot one second after this durable marker.
            # The callback can only begin after the commit, so actual send starts
            # are at least as far apart as these stored reservation timestamps.
            reserved_at = self.wall_clock()
            month = _month_at(reserved_at)
            if month < stored_month:
                raise MwmblLimiterClockError("UTC clock moved backwards; dispatch blocked.")
            if month > stored_month:
                attempts = 0
                stored_month = month
            attempts += 1
            connection.execute(
                "INSERT INTO mwmbl_usage "
                "(singleton, month, attempts, last_dispatch, next_allowed, in_flight) "
                "VALUES (1, ?, ?, ?, ?, 1) "
                "ON CONFLICT(singleton) DO UPDATE SET month=excluded.month, "
                "attempts=excluded.attempts, last_dispatch=excluded.last_dispatch, "
                "next_allowed=excluded.next_allowed, in_flight=1",
                (month, attempts, reserved_at, reserved_at + _MIN_INTERVAL_SECONDS),
            )
            connection.execute("COMMIT")
        except MwmblLimiterError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (OSError, sqlite3.Error, OverflowError, ValueError) as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise MwmblLimiterStateError("Unable to reserve Mwmbl quota and rate slot.") from error

    def _finish(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE mwmbl_usage SET in_flight = 0 WHERE singleton = 1")
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise MwmblLimiterStateError("Unable to finalize Mwmbl dispatch state.") from error


def _month_at(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")
    except (OverflowError, OSError, ValueError) as error:
        raise MwmblLimiterClockError("System UTC clock is invalid; dispatch blocked.") from error


def configured_mwmbl_limiter(
    *,
    environ: dict[str, str] | None = None,
    monthly_limit: int = _MAX_MONTHLY_ATTEMPTS,
) -> MwmblRequestLimiter:
    """Return a limiter backed by the operator's shared host-side ledger.

    Search remains unavailable unless the host worker is explicitly configured
    with ``MWMBL_SHARED_LEDGER_DIRECTORY``. Every process that can dispatch
    through the same egress IP must point this setting at the same local-host
    directory. The skill spool is deliberately not used as an implicit fallback:
    separate installations commonly have separate spools but may share one IP.

    The caller must still run in the host worker's serialized dispatch path.
    This configuration cannot coordinate separate hosts, filesystems, or
    operators that do not share the same directory.
    """
    values = os.environ if environ is None else environ
    configured = values.get(SHARED_LEDGER_DIRECTORY_ENV)
    if not isinstance(configured, str) or not configured.strip():
        raise MwmblLimiterStateError(
            "Mwmbl search is disabled; configure one shared host-side quota ledger first."
        )
    directory = Path(configured).expanduser()
    if not directory.is_absolute():
        raise MwmblLimiterStateError(
            "Mwmbl search is disabled; the shared quota ledger path must be absolute."
        )
    try:
        return MwmblRequestLimiter(directory, monthly_limit=monthly_limit)
    except (OSError, ValueError) as error:
        raise MwmblLimiterStateError(
            "Mwmbl search is disabled because the shared quota ledger is unavailable."
        ) from error
