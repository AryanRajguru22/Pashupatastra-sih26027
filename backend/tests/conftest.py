"""Test isolation for the maintenance-jobs database.

backend/app/jobs/router.py constructs a JobService at module import
time, so merely importing backend.app.api.main (which test_api.py and
the recovery API tests do) opens - and, on a clean checkout, creates -
the repository-root jobs.db.

That means a plain `pytest` run reads and writes the real operational
database, and any future API-level job test would leave rows behind for
the next run to trip over. Pointing PASHUPAT_JOBS_DB at a per-session
temporary file removes that coupling. It is set before any test module
is imported, which is what makes it take effect: DEFAULT_DB_PATH is a
module-level constant resolved at import.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = tempfile.mkdtemp(prefix="pashupatastra-tests-")

os.environ.setdefault(
    "PASHUPAT_JOBS_DB",
    str(Path(_TEST_DB_DIR) / "jobs.db"),
)
