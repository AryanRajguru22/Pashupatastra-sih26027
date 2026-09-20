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
    The actions below (including REJECT_PROPOSAL/POSTPONE_PROPOSAL,
    added in Sprint 3 Slice 3) all pass through this same seam, but no
    policy that actually restricts who may call which action ships yet -
    that is still a later slice's work.
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

    # Sprint 3 Slice 3: authority review of a NEW block proposal.
    # Approval is deliberately NOT a separate action here - it is the
    # same domain commit transition as COMMIT_BLOCK above, and
    # JobService.approve_proposal delegates to the existing commit
    # machinery (notify) rather than introducing a second commit
    # concept guarded by a second permission.
    REJECT_PROPOSAL = "REJECT_PROPOSAL"
    POSTPONE_PROPOSAL = "POSTPONE_PROPOSAL"

    # Sprint 3 Slice 5: field execution of an approved (committed) block.
    # COMPLETE_JOB above is reused for in_progress -> completed - it is
    # already the terminal completion transition's action, and Slice 5
    # gives it evidence rather than a second permission. WORKER starts,
    # completes and reports not-completed; READ_JOB_EXECUTION follows
    # the READ_BLOCK_PROPOSAL precedent for a derived read.
    START_EXECUTION = "START_EXECUTION"
    REPORT_EXECUTION_NOT_COMPLETED = "REPORT_EXECUTION_NOT_COMPLETED"
    READ_JOB_EXECUTION = "READ_JOB_EXECUTION"

    # Sprint 3 Slice 6: authority release of an already approved
    # (committed) block whose execution cannot begin. Deliberately its
    # OWN action rather than a reuse of REJECT_PROPOSAL: rejection
    # refuses an uncommitted proposal, while this withdraws a commitment
    # the authority has already made - a strictly larger decision, and
    # one a future policy must be able to grant separately.
    RELEASE_COMMITTED_BLOCK = "RELEASE_COMMITTED_BLOCK"

    # Sprint 3 Slice 9: accountability reads. Both follow the
    # READ_BLOCK_PROPOSAL / READ_JOB_EXECUTION precedent - a derived read
    # passes the same seam as every other read - and both are READ
    # actions only. There is deliberately no ACKNOWLEDGE_ESCALATION or
    # SILENCE_NOTIFICATION action: acknowledgement without authentication
    # is meaningless (anyone could acknowledge anything), and a silence
    # switch is a way to hide an operational failure.
    READ_JOB_OBLIGATIONS = "READ_JOB_OBLIGATIONS"
    READ_OPTIMIZATION_RUN = "READ_OPTIMIZATION_RUN"


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
