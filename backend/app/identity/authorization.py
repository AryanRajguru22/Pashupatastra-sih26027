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

    The intended role split is no longer only prose: Slice 10.1D encodes
    it as a constant in backend.app.identity.role_actions and a policy
    that consults it in backend.app.identity.policy. Neither is WIRED.
    UnenforcedPolicy is still what this application constructs by
    default (backend.app.jobs.service.JobService.__init__), so the
    shipped behaviour is unchanged: no permission is enforced today. Do
    not describe it otherwise.

RESOURCES (Slice 10.1D)
    Authorization is asked two questions, in two methods, in this order:

        authorize(actor, action)                  may this KIND of action
        authorize_resource(actor, action, loc)    on THIS railway resource

    The first is cheap and runs before any repository read, so an actor
    holding no such permission is refused without causing database work.
    The second runs only after the resource has been loaded and resolved
    to a backend.app.identity.resource.ResourceLocation, because a
    railway scope cannot be evaluated against a job whose section is not
    yet known.

    THE CONSTRUCTION-TIME GUARANTEE (Slice 10.1D.1)
        An enforcing policy that cannot answer the second question
        cannot be installed in a JobService at all - see
        require_resource_aware and JobService.authorization. The
        guarantee is attached to the service's own construction
        boundary, so it holds for every entrypoint that will ever build
        one, not only for the routes wired today.

    Neither method may look at lifecycle state. "May this actor attempt
    this?" and "is this valid from the current state?" are different
    questions with different homes: the second belongs to
    backend.app.jobs.lifecycle, which records a refused attempt as
    TRANSITION_REJECTED. An authorization denial deliberately records
    nothing and changes nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Protocol

from backend.app.identity.actor import Actor
from backend.app.identity.resource import ResourceLocation


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

    # Slice 10.1D: reading a job itself. The ONLY action this seam was
    # missing, and the 10.1D gate proved it from code rather than from a
    # wish list: GET /v1/jobs and GET /v1/jobs/{job_id} reached
    # JobRepository directly, with no actor and no authorization call,
    # while every other read in the application already passed here.
    READ_JOB = "READ_JOB"


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

    `enforcing` is the policy's own claim to be a COMPLETE authorization
    control - one a deployment may rely on to decide who may touch what -
    so no caller has to guess from the class name. It is the hinge the
    construction guard turns on (see require_resource_aware below and
    backend.app.jobs.service.JobService.authorization): a policy claiming
    to restrict resources must be able to decide one. A policy that is
    merely an instrument for exercising the role/action seam declares
    `enforcing = False` and says so by subclassing RoleActionOnlyPolicy.

    TWO QUESTIONS, TWO METHODS (Slice 10.1D)
        authorize           may this actor perform this KIND of action?
        authorize_resource  may it perform it on THIS railway resource?

    They are deliberately separate methods rather than one call with an
    optional resource. A single `authorize(actor, action, resource=None)`
    would mean a caller that forgets the resource silently performs no
    scope check and is permitted - an invisible failure with no error and
    full access. Two methods make the omission impossible to write by
    accident, and `require_resource_aware` below makes it impossible to
    ship.
    """

    enforcing: bool

    def authorize(self, actor: Actor, action: JobAction) -> None:
        """Return normally to permit; raise AuthorizationDenied to refuse."""
        ...

    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None:
        """Decide the same action against one resource's location.

        Called only AFTER `authorize` has permitted the action and the
        resource has been loaded and resolved. Return normally to permit;
        raise AuthorizationDenied to refuse.

        It receives a ResourceLocation and nothing else: no job row, no
        status, no evidence. Lifecycle state is not authorization's
        question (see backend.app.jobs.lifecycle), and a policy that
        cannot see the state cannot start branching on it.
        """
        ...


class ResourceAwarePolicy(ABC):
    """Base for any policy that actually restricts something.

    Subclassing is the structural half of the guarantee: `abstractmethod`
    means a subclass that implements only the old two-argument
    `authorize` cannot be INSTANTIATED at all - Python raises TypeError
    at construction, before such a policy can reach a JobService.

    `require_resource_aware` is the other half, for a policy that was
    never built on this base.
    """

    enforcing = True

    @abstractmethod
    def authorize(self, actor: Actor, action: JobAction) -> None: ...

    @abstractmethod
    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None: ...


class RoleActionOnlyPolicy(ABC):
    """Answers the role/action question ONLY. Never a deployment's control.

    THE SHAPE THIS EXISTS TO NAME (Slice 10.1D.1)
        The suite has long contained policies that implement the
        two-argument `authorize` and nothing else, in order to prove one
        narrow property of the seam: that the role/action question is
        asked, is asked exactly once, and is asked before any read or
        write happens. They are instruments for testing the seam, not
        authorization controls anyone relies on.

        Before 10.1D.1 they declared `enforcing = True`, and that made
        "enforcing" useless as a discriminator: JobService could not
        refuse an enforcing policy with no `authorize_resource` without
        refusing them too. This base is the clean distinction the
        construction guard needed.

    WHY `enforcing` IS FALSE HERE, AND WHY THAT IS NOT A LIE
        `enforcing` is a policy's own claim that it is a complete
        authorization control - that a deployment may rely on it to
        decide who may touch what. A role/action-only policy is not one.
        It may refuse an individual action (that is how a denial test
        exercises the seam), but it answers no resource question at all,
        so it restricts nothing about WHICH railway resources an actor
        may reach. `enforcing = True` would be the false claim here, not
        `enforcing = False`.

        The direction matters. A policy that refuses MORE than it claims
        fails closed and cannot become a bypass. The dangerous claim -
        the one 10.1D.1 closes - is the opposite: a policy that declares
        it restricts resources and then silently decides none.

    NOT FOR PRODUCTION
        Nothing under backend/app may subclass this, and
        test_slice10_1d_1_construction_guard.py asserts that from source.
        A deployment that wants enforcement subclasses
        ResourceAwarePolicy, which cannot be instantiated without both
        methods.
    """

    enforcing = False

    @abstractmethod
    def authorize(self, actor: Actor, action: JobAction) -> None: ...


class UnenforcedPolicy:
    """Permits everything. Still the only policy this application ships.

    Present so the seam is exercised on every call rather than added
    later around code that never passed through it. Slice 10.1D gave it
    the second method for the same reason: the resource seam is called on
    every resource-shaped action from the day it exists, so nothing is
    retrofitted around code that never passed through it.

    It is resource-aware (both methods exist) and NOT enforcing, which is
    what keeps it installable while enforcing nothing.
    """

    enforcing = False

    def authorize(self, actor: Actor, action: JobAction) -> None:
        return None

    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None:
        return None


class PolicyConfigurationError(RuntimeError):
    """A policy was wired in that cannot answer the resource question."""


def is_resource_aware(policy: object) -> bool:
    """Whether this policy can be asked the resource question at all."""

    return callable(getattr(policy, "authorize_resource", None))


def require_resource_aware(policy: object) -> object:
    """Refuse an enforcing policy that cannot decide a resource. Returns it.

    WHERE THIS RUNS (Slice 10.1D.1)
        At the CONSTRUCTION boundary of the service that will ask the
        question: backend.app.jobs.service.JobService installs every
        policy through it, in __init__ and in the `authorization`
        property setter alike. There is therefore no way to place an
        enforcing policy with no `authorize_resource` into a JobService -
        not through a new production entrypoint, not through a later
        reassignment - and no code path in which the resource question is
        silently skipped for a policy that claims to restrict resources.

        backend.app.jobs.router calls it again on its own service. That
        call is now redundant by construction and is kept deliberately as
        defence in depth: a deployment reading the wiring sees the
        requirement stated where it chooses its policy.

    A non-enforcing policy is left alone - it restricts nothing that this
    guard could protect, by its own declaration, so it cannot be a scope
    bypass. That is what keeps the two-argument role/action-seam doubles
    (RoleActionOnlyPolicy) installable while an enforcing half-built
    policy is not.
    """

    if getattr(policy, "enforcing", False) and not is_resource_aware(policy):
        raise PolicyConfigurationError(
            f"{type(policy).__name__} declares enforcing=True but implements "
            "no authorize_resource, so every railway scope check would be "
            "skipped. An enforcing policy must implement both methods - "
            "subclass ResourceAwarePolicy."
        )

    return policy


__all__ = [
    "AuthorizationDenied",
    "AuthorizationPolicy",
    "JobAction",
    "PolicyConfigurationError",
    "ResourceAwarePolicy",
    "RoleActionOnlyPolicy",
    "UnenforcedPolicy",
    "is_resource_aware",
    "require_resource_aware",
]
