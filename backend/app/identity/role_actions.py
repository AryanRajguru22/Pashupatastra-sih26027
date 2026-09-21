"""Which railway role may attempt which job action (Slice 10.1D).

A constant, not an engine. There is no rule language, no condition, no
inheritance between roles and no priority ordering: a role either holds
an action or it does not. Everything here is data that
backend.app.identity.policy reads; nothing here decides anything, reads a
repository or knows what a railway section is.

WHAT THIS TABLE IS
    The role split the authorization module has described in prose since
    Sprint 3 Slice 1 - WORKER reports and executes, AUTHORITY approves,
    postpones, rejects and releases, ENGINEER optimizes - written down
    once so a policy can consult it instead of re-deriving it.

WHAT IT IS NOT
    Enforcement, and not the shipped default. JobService still
    constructs UnenforcedPolicy; this table only matters to a deployment
    that explicitly installs an enforcing policy.

FAIL CLOSED BY OMISSION
    A role's entry lists everything it may attempt. Anything absent is
    denied, so a new JobAction is refused for every role until somebody
    grants it deliberately. Three consequences are intended, not
    oversights:

    ADMIN holds NOTHING. It is the Pashupatastra administrative role, not
    an operational one, and the 10.1 readiness audit settled that it
    carries no railway scope either. An administrative read surface is a
    later policy decision; a role that holds no action and no scope
    cannot quietly become a super-user in the meantime.

    UNIDENTIFIED holds NOTHING. It records that a caller supplied no
    identity at all, which is the opposite of a permission.

    SYSTEM holds NOTHING. No current code path asks this seam about a
    SYSTEM actor: the application's own actors (OPTIMIZER and friends)
    are recorded ON events by backend.app.jobs.lifecycle, they do not
    call authorize, and the HTTP layer refuses a caller-declared SYSTEM
    role outright (backend.app.api.deps, 403). If a future internal
    caller genuinely needs to pass this seam, that is an explicit grant.

    ASSIGN_SCHEDULE is granted to NO role. It writes a placement directly,
    bypassing the optimizer, and no HTTP route reaches it. Leaving it
    ungranted means an enforcing policy denies it by default, which is
    the fail-closed answer while nothing needs it.

UNRESOLVED QUESTIONS, ANSWERED WITH THE NARROW DEFAULT
    OQ-1 (may ENGINEER or AUTHORITY report a job?) and OQ-2 (does holding
    one section permit corridor-wide actions?) are recorded as open in
    docs/SLICE10_1D_RESOURCE_AUTHORIZATION_ARCHITECTURE.md. Nothing in
    the code base answers either. Both take the narrowest reading here -
    REPORT_JOB is WORKER-only, READ_OPTIMIZATION_RUN is ENGINEER-only -
    because widening a grant later is a decision, while narrowing one
    that has already shipped is a regression.
"""

from __future__ import annotations

from typing import FrozenSet, Mapping

from backend.app.identity.actor import ActorRole
from backend.app.identity.authorization import JobAction

# Reads that name one job. Every operational role holds all of them:
# nothing in the code base distinguishes who may READ a job they are
# already scoped to, and railway scope - not role - is what keeps a
# reader out of another section's work.
JOB_READS: FrozenSet[JobAction] = frozenset(
    {
        JobAction.READ_JOB,
        JobAction.READ_JOB_HISTORY,
        JobAction.READ_BLOCK_PROPOSAL,
        JobAction.READ_JOB_EXECUTION,
        JobAction.READ_JOB_OBLIGATIONS,
    }
)


ROLE_ACTIONS: Mapping[ActorRole, FrozenSet[JobAction]] = {
    ActorRole.WORKER: frozenset(
        {
            JobAction.REPORT_JOB,
            JobAction.START_EXECUTION,
            JobAction.COMPLETE_JOB,
            JobAction.REPORT_EXECUTION_NOT_COMPLETED,
        }
    )
    | JOB_READS,
    ActorRole.ENGINEER: frozenset(
        {
            JobAction.REQUEST_OPTIMIZATION,
            JobAction.READ_OPTIMIZATION_RUN,
        }
    )
    | JOB_READS,
    ActorRole.AUTHORITY: frozenset(
        {
            JobAction.COMMIT_BLOCK,
            JobAction.REJECT_PROPOSAL,
            JobAction.POSTPONE_PROPOSAL,
            JobAction.RELEASE_COMMITTED_BLOCK,
        }
    )
    | JOB_READS,
    ActorRole.ADMIN: frozenset(),
    ActorRole.SYSTEM: frozenset(),
    ActorRole.UNIDENTIFIED: frozenset(),
}


# Actions decided against one corridor rather than one section. Their
# resources carry no section at all, so section matching would report
# every one of them UNRESOLVED and deny everything; they take the
# corridor rule in backend.app.identity.policy instead.
CORRIDOR_SCOPED_ACTIONS: FrozenSet[JobAction] = frozenset(
    {
        JobAction.REQUEST_OPTIMIZATION,
        JobAction.READ_OPTIMIZATION_RUN,
    }
)


# Actions decided against one job's section. This is every remaining
# action, listed rather than derived so that adding a JobAction without
# classifying it is caught by a test instead of defaulting into one of
# the two rules.
SECTION_SCOPED_ACTIONS: FrozenSet[JobAction] = frozenset(
    {
        JobAction.REPORT_JOB,
        JobAction.COMMIT_BLOCK,
        JobAction.REJECT_PROPOSAL,
        JobAction.POSTPONE_PROPOSAL,
        JobAction.RELEASE_COMMITTED_BLOCK,
        JobAction.START_EXECUTION,
        JobAction.COMPLETE_JOB,
        JobAction.REPORT_EXECUTION_NOT_COMPLETED,
        JobAction.ASSIGN_SCHEDULE,
    }
) | JOB_READS


def permitted_actions(role: ActorRole) -> FrozenSet[JobAction]:
    """Everything this role may attempt. Empty for an unknown role."""

    return ROLE_ACTIONS.get(role, frozenset())


def role_permits(role: ActorRole, action: JobAction) -> bool:
    """Whether the role holds the action at all. No scope, no state."""

    return action in permitted_actions(role)


__all__ = [
    "CORRIDOR_SCOPED_ACTIONS",
    "JOB_READS",
    "ROLE_ACTIONS",
    "SECTION_SCOPED_ACTIONS",
    "permitted_actions",
    "role_permits",
]
