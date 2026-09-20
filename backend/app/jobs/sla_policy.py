"""SLA policy for maintenance-job obligations (Sprint 3 Slice 9).

WHAT THIS IS
    The one place the numbers live. A versioned, frozen, injectable
    policy that answers two questions and nothing else:

      1. how long is the allowed response time for a given job, and
      2. given a clock start and a moment of evaluation, how far past
         that allowance are we?

    It follows the AssetAssociationPolicy / DuplicatePolicy precedent
    from Slice 8: a frozen dataclass with documented defaults, injected
    on JobService, never a literal buried in derivation logic.

*** THE VALUES IN THIS MODULE ARE ASSUMED DEMO ENGINEERING VALUES. ***

    ASSUMED_DEMO_SLA_POLICY below is NOT Indian Railways policy, is not
    derived from any IR circular, schedule of powers, maintenance manual
    or corridor agreement, and has not been reviewed or approved by any
    railway authority. It is a set of plausible engineering placeholders
    chosen so the accountability model can be demonstrated end to end.
    Every value it produces carries `version` and `assumed=True`
    (SlaPolicy.assumed) precisely so no report, dashboard or export can
    present these durations as verified operational policy.

    SlaPolicy.__post_init__ REFUSES to construct a policy whose version
    does not say so - see ASSUMED_VERSION_MARKER. Until a real,
    attributable source exists, the type itself keeps the claim honest;
    the moment one does, that check is the single place the new
    provenance is recorded.

WALL CLOCK, DELIBERATELY
    Every duration here is wall-clock elapsed time. There is no working
    calendar, no shift model, no weekend rule and no holiday list
    anywhere in this repository, so there is nothing to compute
    operational time against. Inventing an IR holiday calendar would be
    fabricated domain data. Maintenance possession is largely a night
    activity, so operational time is genuinely the better long-term
    answer - it is deferred for lack of data, not because it is wrong.

ONE DIMENSION OF VARIATION
    Severity, and only severity - backend.app.data.models.DefectSeverity,
    the enum already recorded at intake as `reported_severity`. No second
    severity vocabulary is created here. Corridor, job type, authority
    and maintenance category are deliberately NOT policy dimensions yet:
    SlaPolicySet.resolve_sla(job) is the seam they are added at later,
    without any caller or any obligation derivation changing.

WHAT THIS MODULE DOES NOT DO
    It does not read the database, know what an obligation is, name a
    responsible role, or decide whether an obligation exists. It is pure
    arithmetic over durations. backend.app.jobs.obligations owns the
    domain meaning; this module owns the numbers and the ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Mapping, Optional

from backend.app.data.models import DefectSeverity


# A policy version must contain this marker. See the module docstring:
# it is what makes "these numbers are assumed" unforgeable rather than a
# comment somebody can delete.
ASSUMED_VERSION_MARKER = "assumed"

# The number of escalation levels the obligation state vocabulary can
# name (ESCALATED_L1..L3 - see backend.app.jobs.obligations.
# ObligationState). A policy with more steps than the vocabulary can
# express is refused at construction rather than silently clamped to L3,
# which would report a level-4 breach as a level-3 one.
MAX_ESCALATION_STEPS = 3


class InvalidSlaPolicyError(ValueError):
    """A policy's own values are not a coherent SLA policy.

    Raised at construction, never at evaluation: an incoherent policy can
    never reach a derivation and produce a deadline nobody can explain.
    """


@dataclass(frozen=True)
class SlaTiming:
    """Pure timing facts for one clock start under one policy.

    Deliberately names no obligation state. This module knows about
    deadlines and elapsed time; backend.app.jobs.obligations maps those
    facts onto the domain vocabulary (WITHIN_SLA / DUE_SOON / OVERDUE /
    ESCALATED_Ln), so neither module has to duplicate the other's enum.

    escalation_level is the number of this policy's escalation
    thresholds that `evaluated_at` has reached: 0 before the first one,
    then 1, 2 or 3. Monotonic in elapsed time, by construction.
    """

    started_at: datetime
    evaluated_at: datetime
    sla: timedelta
    due_at: datetime
    due_soon_at: datetime
    elapsed: timedelta
    escalation_level: int

    @property
    def is_due_soon(self) -> bool:
        return self.evaluated_at >= self.due_soon_at

    @property
    def is_past_due(self) -> bool:
        return self.evaluated_at >= self.due_at


@dataclass(frozen=True)
class SlaPolicy:
    """The response-time allowances that apply to one job. Immutable.

    approval_sla           authority decision on a current proposal
    execution_start_sla    field work beginning on an approved block
    completion_sla         field work concluding, one way or the other

    due_soon_fraction      the fraction of an SLA at which DUE_SOON begins
    escalation_multipliers multiples of the SLA at which L1, L2, L3 begin

    `assumed` is True for every policy this repository ships and is
    reported alongside every derived obligation. See the module
    docstring for why it cannot currently be False.
    """

    version: str
    approval_sla: timedelta
    execution_start_sla: timedelta
    completion_sla: timedelta
    due_soon_fraction: float = 0.75
    escalation_multipliers: tuple[float, ...] = (1.0, 2.0, 4.0)
    assumed: bool = True

    def __post_init__(self) -> None:
        if not self.version or not self.version.strip():
            raise InvalidSlaPolicyError("An SLA policy must carry a version.")

        if not self.assumed:
            raise InvalidSlaPolicyError(
                "No verified (non-assumed) SLA policy exists in this "
                "repository. Every SLA value shipped here is an assumed "
                "engineering placeholder, not Indian Railways policy; "
                "assumed=False would be a claim nothing supports."
            )

        if ASSUMED_VERSION_MARKER not in self.version.lower():
            raise InvalidSlaPolicyError(
                f"SLA policy version {self.version!r} must contain "
                f"{ASSUMED_VERSION_MARKER!r}: these durations are assumed "
                "demo values, and every record that cites a version must "
                "say so on its face."
            )

        for name in ("approval_sla", "execution_start_sla", "completion_sla"):
            value = getattr(self, name)

            if not isinstance(value, timedelta):
                raise InvalidSlaPolicyError(f"{name} must be a timedelta.")

            if value <= timedelta(0):
                raise InvalidSlaPolicyError(f"{name} must be positive; got {value}.")

        if not 0.0 < self.due_soon_fraction <= 1.0:
            raise InvalidSlaPolicyError(
                "due_soon_fraction must lie in (0.0, 1.0]; got "
                f"{self.due_soon_fraction}."
            )

        steps = tuple(float(step) for step in self.escalation_multipliers)
        object.__setattr__(self, "escalation_multipliers", steps)

        if not steps:
            raise InvalidSlaPolicyError(
                "An SLA policy must define at least one escalation step."
            )

        if len(steps) > MAX_ESCALATION_STEPS:
            raise InvalidSlaPolicyError(
                f"An SLA policy may define at most {MAX_ESCALATION_STEPS} "
                "escalation steps, because the obligation state vocabulary "
                f"names exactly that many; got {len(steps)}."
            )

        if steps[0] < 1.0:
            raise InvalidSlaPolicyError(
                "The first escalation step must be at or after the deadline "
                f"(multiplier >= 1.0); got {steps[0]}."
            )

        if list(steps) != sorted(set(steps)):
            raise InvalidSlaPolicyError(
                f"Escalation steps must be strictly increasing; got {steps}."
            )

    def timing(
        self,
        *,
        sla: timedelta,
        started_at: datetime,
        evaluated_at: datetime,
    ) -> SlaTiming:
        """Where `evaluated_at` sits relative to a clock started at `started_at`.

        A pure function of its three arguments and this policy's own
        values: the same inputs always produce the same SlaTiming, which
        is what makes a derived escalation reproducible by an auditor
        (and what lets a future sweep call exactly this code).

        `sla` is passed in rather than selected here because WHICH of the
        three allowances applies is an obligation question, not a policy
        one - see backend.app.jobs.obligations.

        Both moments must be timezone-aware; a naive datetime is refused
        rather than assumed to be UTC.
        """

        for label, moment in (
            ("started_at", started_at),
            ("evaluated_at", evaluated_at),
        ):
            if moment.tzinfo is None:
                raise InvalidSlaPolicyError(
                    f"{label} must be timezone-aware; got {moment!r}."
                )

        due_at = started_at + sla
        due_soon_at = started_at + sla * self.due_soon_fraction

        level = 0
        for step in self.escalation_multipliers:
            if evaluated_at >= started_at + sla * step:
                level += 1

        return SlaTiming(
            started_at=started_at,
            evaluated_at=evaluated_at,
            sla=sla,
            due_at=due_at,
            due_soon_at=due_soon_at,
            # Never negative: a clock that appears to start in the future
            # (skew, or a policy applied to a re-read event) reports zero
            # elapsed rather than a negative duration nothing can render.
            elapsed=max(evaluated_at - started_at, timedelta(0)),
            escalation_level=level,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "assumed": self.assumed,
            "approval_sla_seconds": self.approval_sla.total_seconds(),
            "execution_start_sla_seconds": self.execution_start_sla.total_seconds(),
            "completion_sla_seconds": self.completion_sla.total_seconds(),
            "due_soon_fraction": self.due_soon_fraction,
            "escalation_multipliers": list(self.escalation_multipliers),
        }


@dataclass(frozen=True)
class SlaPolicySet:
    """One SlaPolicy per DefectSeverity, resolved from a job's own report.

    Every member shares the set's `version`: a version names the WHOLE
    table of numbers, not one row of it, so an obligation that reports
    `policy_version` identifies exactly which set of durations produced
    its deadline.
    """

    version: str
    by_severity: Mapping[DefectSeverity, SlaPolicy] = field(repr=False)

    def __post_init__(self) -> None:
        missing = [s for s in DefectSeverity if s not in self.by_severity]

        if missing:
            raise InvalidSlaPolicyError(
                "An SLA policy set must resolve every DefectSeverity; "
                f"missing {[s.value for s in missing]}."
            )

        for severity, policy in self.by_severity.items():
            if policy.version != self.version:
                raise InvalidSlaPolicyError(
                    f"Policy for {severity.value} carries version "
                    f"{policy.version!r}, not the set's {self.version!r}; a "
                    "version must name the whole table of numbers."
                )

        # Read-only view: a frozen dataclass still holds a mutable dict
        # unless the mapping itself refuses writes.
        object.__setattr__(
            self, "by_severity", MappingProxyType(dict(self.by_severity))
        )

    def for_severity(self, severity: Optional[DefectSeverity]) -> SlaPolicy:
        """The policy for one severity. An absent severity resolves to NONE."""

        return self.by_severity[severity or DefectSeverity.NONE]

    def resolve_sla(self, job: Mapping[str, Any]) -> SlaPolicy:
        """The policy governing this job, from the severity it was reported with.

        Reads block_candidate.metadata.reported_severity - the WORKER's
        own assessment, recorded verbatim at intake (see
        JobService.create_job). A job reported before Slice 2, or by a
        caller that supplied no severity, carries None and resolves to
        the DefectSeverity.NONE policy.

        An UNRECOGNISED stored value is refused (InvalidSlaPolicyError)
        rather than quietly treated as NONE: a severity this policy set
        cannot resolve means the stored job and this vocabulary disagree,
        and guessing would attach a deadline nobody can justify.
        """

        metadata = (job.get("block_candidate") or {}).get("metadata") or {}
        raw = metadata.get("reported_severity")

        if raw is None:
            return self.for_severity(DefectSeverity.NONE)

        try:
            severity = DefectSeverity(raw)
        except ValueError as exc:
            raise InvalidSlaPolicyError(
                f"Job {job.get('job_id')!r} carries reported_severity "
                f"{raw!r}, which is not a DefectSeverity; no SLA can be "
                "resolved for it."
            ) from exc

        return self.for_severity(severity)


# ----------------------------------------------------------------------
# The shipped default. ASSUMED DEMO VALUES - see the module docstring.
# ----------------------------------------------------------------------

# Bumped whenever ANY number below changes. Recorded on every derived
# obligation and in every notification intent key, so a past escalation
# can be shown to have been computed under exactly these durations.
ASSUMED_DEMO_SLA_VERSION = "sla-assumed-demo-1.0"


def _assumed(
    approval_hours: float,
    execution_start_hours: float,
    completion_hours: float,
) -> SlaPolicy:
    """One severity row of ASSUMED_DEMO_SLA_POLICY. Not railway policy."""

    return SlaPolicy(
        version=ASSUMED_DEMO_SLA_VERSION,
        approval_sla=timedelta(hours=approval_hours),
        execution_start_sla=timedelta(hours=execution_start_hours),
        completion_sla=timedelta(hours=completion_hours),
    )


# The only SLA policy this repository ships.
#
# ASSUMED DEMO ENGINEERING VALUES. Not Indian Railways policy, not
# derived from any IR source, not reviewed by any railway authority.
# Chosen to be plausible and to make the accountability model
# demonstrable - nothing more. See the module docstring.
#
#   severity  | approval | execution start | completion
#   CRITICAL  |       2h |              1h |         4h
#   MODERATE  |       6h |              4h |        12h
#   MINOR     |      24h |             12h |        24h
#   NONE      |      24h |             12h |        24h
#
# DUE_SOON begins at 75% of the applicable SLA. Escalation levels begin
# at 1x (the deadline itself), 2x and 4x the applicable SLA.
ASSUMED_DEMO_SLA_POLICY = SlaPolicySet(
    version=ASSUMED_DEMO_SLA_VERSION,
    by_severity={
        DefectSeverity.CRITICAL: _assumed(2, 1, 4),
        DefectSeverity.MODERATE: _assumed(6, 4, 12),
        DefectSeverity.MINOR: _assumed(24, 12, 24),
        DefectSeverity.NONE: _assumed(24, 12, 24),
    },
)


def resolve_sla(
    job: Mapping[str, Any],
    policy: SlaPolicySet = ASSUMED_DEMO_SLA_POLICY,
) -> SlaPolicy:
    """The SlaPolicy governing one job. See SlaPolicySet.resolve_sla."""

    return policy.resolve_sla(job)


__all__ = [
    "ASSUMED_DEMO_SLA_POLICY",
    "ASSUMED_DEMO_SLA_VERSION",
    "ASSUMED_VERSION_MARKER",
    "MAX_ESCALATION_STEPS",
    "InvalidSlaPolicyError",
    "SlaPolicy",
    "SlaPolicySet",
    "SlaTiming",
    "resolve_sla",
]
