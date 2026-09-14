"""The authorization seam for job actions (Sprint 3 Slice 1).

WHAT THIS IS
    The single place a future RBAC policy is consulted. Every
    state-changing JobService method (and the job history read) calls
    `authorize(actor, action)` BEFORE it reads or writes anything, so a
    denying policy stops an action with no state change and no event.
    test_job_lifecycle_accountability.py proves that with a denying
    test policy.

WHAT THIS IS NOT
    Enforcement. The only policy that ships is UnenforcedPolicy, which
    permits every action for every actor and says so in its name and in
    `enforcing = False`. No permission described anywhere in this
    repository is enforced today. Do not describe it otherwise.

    The intended role split - WORKER reports and completes, AUTHORITY
    approves/postpones/rejects, SYSTEM scores and optimizes, ADMIN
    manages identities - is deliberately NOT encoded as a table here.
    The approval actions do not exist yet, and a permission table with
    no action to guard would be speculative code that later slices
    would have to reconcile rather than build on.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from backend.app.identity.actor import Actor


class JobAction(str, Enum):
    """Every job-domain operation that passes the authorization seam."""

    REPORT_JOB = "REPORT_JOB"
    REQUEST_OPTIMIZATION = "REQUEST_OPTIMIZATION"
    ASSIGN_SCHEDULE = "ASSIGN_SCHEDULE"
    COMMIT_BLOCK = "COMMIT_BLOCK"
    COMPLETE_JOB = "COMPLETE_JOB"
    READ_JOB_HISTORY = "READ_JOB_HISTORY"
    READ_BLOCK_PROPOSAL = "READ_BLOCK_PROPOSAL"


class AuthorizationDenied(Exception):
    """A policy refused an action. Raised before any state is touched.

    Deliberately not a PermissionError: that is an OSError subclass
    describing filesystem/OS permission failures, not a domain decision.
    """

    def __init__(self, actor: Actor, action: JobAction, detail: str = ""):
        self.actor = actor
        self.action = action
        message = (
            f"{actor.role.value} actor {actor.actor_id!r} is not permitted "
            f"to perform {action.value}"
        )
        super().__init__(f"{message}: {detail}" if detail else message)


class AuthorizationPolicy(Protocol):
    """Anything that can decide whether an actor may perform an action.

    `enforcing` states whether the policy actually restricts anything,
    so no caller has to guess from the class name.
    """

    enforcing: bool

    def authorize(self, actor: Actor, action: JobAction) -> None:
        """Return normally to permit; raise AuthorizationDenied to refuse."""
        ...


class UnenforcedPolicy:
    """Permits everything. The only policy that exists today.

    Present so the seam is exercised on every call rather than added
    later around code that never passed through it.
    """

    enforcing = False

    def authorize(self, actor: Actor, action: JobAction) -> None:
        return None


__all__ = [
    "AuthorizationDenied",
    "AuthorizationPolicy",
    "JobAction",
    "UnenforcedPolicy",
]
