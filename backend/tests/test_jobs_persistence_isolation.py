"""The jobs database must be isolated, closeable, and overridable.

Two problems this pins down:

1. Importing backend.app.api.main constructs a JobService at module
   import, which opens (and on a clean checkout creates) the
   repository-root jobs.db. A test run must not touch the real
   operational database.

2. `with sqlite3.connect(...) as conn:` opens a TRANSACTION context, not
   a closing one - it commits but never closes. Leaked handles keep the
   database file locked, which on Windows makes temporary test databases
   undeletable.
"""

from __future__ import annotations

import gc
import os
import sqlite3
from pathlib import Path

from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.repository import DEFAULT_DB_PATH, JobRepository
from backend.app.jobs.service import JobService


def make_request() -> JobCreateRequest:
    return JobCreateRequest(
        track_id="UP-1",
        job_type="OHE_MAINTENANCE",
        distance_start=10000,
        distance_end=11000,
        workers_min=2,
        workers_max=4,
        description="Isolation check",
    )


def test_tests_do_not_use_the_repository_root_database():
    """conftest.py must have redirected the default path."""

    assert os.environ.get("PASHUPAT_JOBS_DB")

    repo_root_db = Path(__file__).resolve().parents[2] / "jobs.db"

    assert DEFAULT_DB_PATH != repo_root_db, (
        "tests are pointed at the real operational jobs.db"
    )


def test_importing_the_api_does_not_write_to_the_repository_root_db():
    """Importing the app must not create a database in the repo root."""

    repo_root_db = Path(__file__).resolve().parents[2] / "jobs.db"
    existed_before = repo_root_db.exists()

    import backend.app.api.main  # noqa: F401

    assert repo_root_db.exists() == existed_before, (
        "importing the API created or removed the repository-root jobs.db"
    )


def test_db_path_honours_the_environment_override(monkeypatch, tmp_path):
    """The override is what makes isolation possible at all."""

    target = tmp_path / "override.db"
    monkeypatch.setenv("PASHUPAT_JOBS_DB", str(target))

    import importlib

    import backend.app.jobs.repository as repository_module

    reloaded = importlib.reload(repository_module)

    try:
        assert reloaded.DEFAULT_DB_PATH == target
    finally:
        monkeypatch.delenv("PASHUPAT_JOBS_DB", raising=False)
        importlib.reload(repository_module)


def test_repository_does_not_leak_connections(tmp_path):
    """Every operation must leave the database file releasable.

    Before the fix this left one open handle per call, and the unlink
    below failed on Windows with WinError 32.
    """

    db_path = tmp_path / "leak.db"

    service = JobService(repository=JobRepository(db_path))

    job = service.create_job(make_request())
    service.list_jobs()
    service.set_schedule(job["job_id"], 100, 160)
    service.notify(job["job_id"])
    service.complete(job["job_id"])

    del service
    gc.collect()

    # If any connection is still open this raises PermissionError.
    db_path.unlink()

    assert not db_path.exists()


def test_two_repositories_on_separate_files_do_not_share_rows(tmp_path):
    """Isolation is per-file, so parallel scenarios cannot contaminate
    one another."""

    first = JobService(repository=JobRepository(tmp_path / "a.db"))
    second = JobService(repository=JobRepository(tmp_path / "b.db"))

    first.create_job(make_request())

    assert len(first.list_jobs()) == 1
    assert second.list_jobs() == []


def test_created_schema_is_the_expected_single_table(tmp_path):
    """Guard against a second, competing job store appearing."""

    db_path = tmp_path / "schema.db"
    JobRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "maintenance_jobs" in tables
