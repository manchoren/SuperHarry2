from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase


class WorkerLockTests(SimpleTestCase):
    def test_only_one_worker_can_hold_a_spool_lock(self):
        from scripts.worker_lock import lock_spool

        with TemporaryDirectory() as directory:
            with lock_spool(Path(directory)):
                with self.assertRaises((OSError, BlockingIOError)):
                    with lock_spool(Path(directory)):
                        self.fail("A second worker acquired the same spool")
            with lock_spool(Path(directory)):
                pass
