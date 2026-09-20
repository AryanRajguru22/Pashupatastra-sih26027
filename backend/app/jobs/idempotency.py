"""Intake idempotency for POST /v1/jobs (Slice 8).

WHAT PROBLEM THIS SOLVES - AND WHAT IT DOES NOT
    A retried submission (flaky connection, double tap, client retry) is
    the SAME request sent twice. It must not create two jobs. That is not
    duplicate detection (two workers, possibly the same defect - see
    backend.app.jobs.duplicate_detection - which always creates the job).

THE MECHANISM - no schema change, no new table, column or index
    An idempotent submission gives its JOB_CREATED event a DETERMINISTIC
    event_id derived from (actor_id, idempotency_key). job_events already
    declares event_id UNIQUE, and the job row and its events are written
    in ONE transaction (JobRepository.create, BEGIN IMMEDIATE). So a
    second creation under the same (actor, key) fails on that existing
    constraint and rolls the whole transaction back - job row included -
    whether the two submissions are threads, or processes sharing the
    database file. The loser then looks the winner up by event_id (an
    indexed point read, no scan) and either returns the same job (same
    request) or refuses (a different request under the same key).

    The request is compared by a SHA-256 over its canonical JSON with the
    idempotency_key itself excluded, stored on the JOB_CREATED event.

GUARANTEE, PRECISELY
      - one job per (actor_id, idempotency_key), enforced by the
        database's own UNIQUE constraint, across threads and across
        processes that share one SQLite file;
      - the guarantee has NO expiry: job_events is append-only, so a key
        stays bound to the job it created;
      - the same key with a different request is refused with 409,
        never silently overwritten and never silently replayed.

LIMITS - stated, not hidden
      - The scope is the DECLARED actor_id (X-Actor-Id). Identity is not
        authenticated (IdentityAssurance is DECLARED_UNVERIFIED), so any
        caller who claims an actor_id and knows a key can replay it. What
        they get back is a job that a request identical to theirs
        created - but this is caller-asserted scoping, not access
        control. An unidentified caller (no actor headers) may not use a
        key at all: there would be no scope to key it on.
      - It is tied to the job_events table. If the database file is
        replaced or history is rebuilt, the guarantee goes with it.
      - It is durable in the sense that SQLite is durable. It is NOT a
        distributed idempotency service, and a multi-writer database
        deployment must re-verify the constraint holds there.
      - Replaying returns the job as it is NOW (it may already be
        scheduled or completed), not a frozen copy of the first response.
      - It de-duplicates intake only. It does not protect any later
        lifecycle action, which already carries its own stale-proposal
        and terminal-state protections.
"""

from __future__ import annotations

import hashlib
from typing import Any

from backend.app.jobs.events import canonical_json


class IdempotencyKeyError(ValueError):
    """An idempotency key cannot be used as sent (e.g. no actor headers
    to scope it to). A malformed request: 400."""


class IdempotencyKeyConflictError(IdempotencyKeyError):
    """The key was already used for a materially different request, or
    its job is gone. Fails closed: 409."""


IDEMPOTENCY_EVENT_PREFIX = "EVT-IDEM-"


def request_fingerprint(request_payload: dict[str, Any]) -> str:
    """SHA-256 of the request's canonical JSON, key excluded.

    request_payload is JobCreateRequest.model_dump(mode="json") minus
    idempotency_key; canonical_json is the ONE deterministic
    serialization the events module already defines.
    """

    payload = {k: v for k, v in request_payload.items() if k != "idempotency_key"}

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def idempotency_event_id(actor_id: str, key: str) -> str:
    """The deterministic JOB_CREATED event_id for (actor_id, key).

    Hashed from the canonical JSON of the PAIR, so ("a", "b:c") and
    ("a:b", "c") cannot collide. The raw key never appears in the id.
    """

    digest = hashlib.sha256(
        canonical_json({"actor_id": actor_id, "idempotency_key": key}).encode(
            "utf-8"
        )
    ).hexdigest()

    return f"{IDEMPOTENCY_EVENT_PREFIX}{digest[:32].upper()}"


__all__ = [
    "IDEMPOTENCY_EVENT_PREFIX",
    "IdempotencyKeyConflictError",
    "IdempotencyKeyError",
    "idempotency_event_id",
    "request_fingerprint",
]
