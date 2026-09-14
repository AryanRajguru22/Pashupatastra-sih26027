"""BlockProposal: the NEW scheduling proposal for one maintenance job
(Sprint 3 Slice 2).

WHAT THIS IS
    A read-time, DERIVED representation. There is no `block_proposals`
    table and no second copy of placement state: a proposal is built
    from three things that already exist and are already the source of
    truth for exactly this fact -
      1. the job's own row (backend.app.jobs.repository) - current
         status, placement, block_candidate;
      2. the specific job-history event that produced the CURRENT
         placement (BLOCK_PROPOSED / BLOCK_REPROPOSED /
         COMMITTED_BLOCK_PRESERVED - backend.app.jobs.history), which
         carries the structured explanation facts computed once, at
         optimization time, by backend.app.jobs.optimization.
         _proposal_explanations;
      3. the optimization_runs record for that event's run
         (backend.app.audit.repository), which carries the run's
         canonical four-axis provenance.

    A second, independently-written proposal store would recreate
    exactly the dual-source-of-truth problem
    backend.app.jobs.lifecycle.CommittedStateIntegrityError exists to
    rule out. Deriving instead means a proposal can never disagree with
    the job row or the history it was built from - there is only one
    place either could have been written.

WHAT A PROPOSAL IS NOT
    A commitment, a notification, or an approval. is_committed is
    always False on a BlockProposal returned by current_proposal() for
    a job in status 'scheduled' - see backend.app.jobs.service.
    JobService.current_proposal, which refuses (NoBlockProposalError)
    for any other status rather than let a caller read committed or
    terminal state through the "current proposal" lens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from contracts import BlockStatus

from backend.app.audit.models import OptimizationRunRecord
from backend.app.data.provenance import ProvenanceProfile
from backend.app.jobs.events import JobEvent, JobEventType
from backend.app.jobs.history import StoredJobEvent
from backend.app.jobs.lifecycle import COMMITTED_STATUSES, proposal_run_id_of


_PLACEMENT_EVENT_TYPES = frozenset(
    {
        JobEventType.BLOCK_PROPOSED,
        JobEventType.BLOCK_REPROPOSED,
        JobEventType.COMMITTED_BLOCK_PRESERVED,
    }
)


class NoBlockProposalError(RuntimeError):
    """This job has no current proposal to build a BlockProposal from.

    Distinct from "job not found" (KeyError): the job exists, but is
    either never yet optimized, was considered and left unscheduled, or
    has moved past the proposal stage (notified/completed) - see
    `reason` for which.
    """

    def __init__(self, job_id: str, reason: str):
        self.job_id = job_id
        self.reason = reason
        super().__init__(f"Job '{job_id}' has no current proposal: {reason}")


@dataclass(frozen=True)
class ProposalExplanationItem:
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class BlockProposal:
    """One NEW scheduling proposal for one job. See module docstring."""

    proposal_id: str
    job_id: str
    optimization_run_id: str
    corridor_id: str

    track_id: str
    section_id: Optional[str]

    start_minute: int
    end_minute: int
    duration_minutes: int

    work_type: str
    priority_score: float
    risk_score: float
    objective_score: float

    is_committed: bool

    explanation: tuple[ProposalExplanationItem, ...]
    provenance: ProvenanceProfile

    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "job_id": self.job_id,
            "optimization_run_id": self.optimization_run_id,
            "corridor_id": self.corridor_id,
            "track_id": self.track_id,
            "section_id": self.section_id,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "duration_minutes": self.duration_minutes,
            "work_type": self.work_type,
            "priority_score": self.priority_score,
            "risk_score": self.risk_score,
            "objective_score": self.objective_score,
            "is_committed": self.is_committed,
            "explanation": [item.to_dict() for item in self.explanation],
            "provenance": self.provenance.to_dict(),
            "generated_at": self.generated_at,
        }


def _latest_placement_event(
    job_id: str,
    run_id: str,
    events: Sequence[StoredJobEvent],
) -> JobEvent:
    """The event, for THIS run, that produced the job's current placement.

    Job history can carry more than one event per run (OPTIMIZATION_
    REQUESTED and OPTIMIZATION_COMPLETED always accompany a placement
    event); the placement event itself is unique per (job, run) because
    a run considers a job at most once. Picking the LAST match, not the
    first, is defensive rather than load-bearing under that uniqueness.
    """

    matches = [
        stored.event
        for stored in events
        if stored.event.optimization_run_id == run_id
        and stored.event.event_type in _PLACEMENT_EVENT_TYPES
    ]

    if not matches:
        raise NoBlockProposalError(
            job_id,
            f"no placement event was recorded for optimization run {run_id!r}; "
            "the job row and its history disagree, which should not happen",
        )

    return matches[-1]


def _explanation_items(metadata: Mapping[str, Any]) -> tuple[ProposalExplanationItem, ...]:
    """Turn the placement event's structured metadata into fixed reasons.

    Every item is {code, detail} - never free-form generated text - and
    every code is stable across proposals, so a UI or a test can switch
    on it directly. See backend.app.jobs.optimization.
    _proposal_explanations for where the underlying facts are computed.
    """

    items: list[ProposalExplanationItem] = []

    start = metadata.get("start_minute")
    end = metadata.get("end_minute")

    if start is not None and end is not None:
        # PLACEMENT, not "earliest feasible slot": the solver's objective
        # rewards whether a block is scheduled at all, not where in the
        # horizon it lands. Nothing computed here (or anywhere upstream)
        # verifies earliness or any other optimality property of this
        # placement, so the explanation must not claim one.
        items.append(
            ProposalExplanationItem(
                "PLACEMENT",
                f"Placed at minute {start}-{end}.",
            )
        )

    window_start = metadata.get("possession_window_start_minute")
    window_end = metadata.get("possession_window_end_minute")

    if window_start is not None and window_end is not None:
        items.append(
            ProposalExplanationItem(
                "POSSESSION_WINDOW_REQUIRED",
                f"Requires a possession window from minute {window_start} "
                f"to {window_end}.",
            )
        )

    track_id = metadata.get("track_id")
    section_id = metadata.get("section_id")
    items.append(
        ProposalExplanationItem(
            "RESOURCE_USED",
            f"Uses track {track_id!r}"
            + (f", section {section_id!r}." if section_id else " (no section modelled)."),
        )
    )

    mates = metadata.get("committed_resource_mates") or []
    if mates:
        items.append(
            ProposalExplanationItem(
                "COMMITTED_BLOCKS_RESPECTED",
                "Existing committed work on the same resource was preserved: "
                + ", ".join(mates) + ".",
            )
        )
    else:
        items.append(
            ProposalExplanationItem(
                "COMMITTED_BLOCKS_RESPECTED",
                "No other committed work shares this resource.",
            )
        )

    if metadata.get("train_data_available"):
        trains = metadata.get("trains_avoided") or []
        if trains:
            items.append(
                ProposalExplanationItem(
                    "CONFLICTING_TRAINS_AVOIDED",
                    "Train movements withheld this section from possession "
                    "and were avoided: " + ", ".join(trains) + ".",
                )
            )
        else:
            items.append(
                ProposalExplanationItem(
                    "CONFLICTING_TRAINS_AVOIDED",
                    "No train movement was rejected as a conflict for this "
                    "section in this run.",
                )
            )
    else:
        items.append(
            ProposalExplanationItem(
                "NO_TRAIN_LEVEL_DATA",
                "This corridor's possession windows are generated static "
                "slots; no per-train movement data was consulted.",
            )
        )

    return tuple(items)


def _derive_is_committed(job: Mapping[str, Any]) -> bool:
    """Whether the job's OWN stored state shows it as committed.

    build_block_proposal must not simply trust a caller's enforcement
    (JobService.current_proposal only ever calls it for status
    'scheduled') - that would make is_committed a fact about who called
    in, not about the job. Checking the job's status alongside its
    block's own status/is_committed flag means a committed job passed
    into this function - directly, or via a future caller with a wider
    status range - is reported as committed rather than silently
    mislabeled as an open proposal.
    """

    block = job.get("block_candidate") or {}
    return (
        job.get("status") in COMMITTED_STATUSES
        or block.get("status") == BlockStatus.COMMITTED.value
        or block.get("is_committed") is True
    )


def build_block_proposal(
    job: Mapping[str, Any],
    corridor_id: str,
    events: Sequence[StoredJobEvent],
    run: OptimizationRunRecord,
) -> BlockProposal:
    """Build the current BlockProposal for a job in status 'scheduled'.

    Raises NoBlockProposalError if the job's own history does not carry
    a placement event for the run its `proposal_run_id` names - an
    integrity signal, not an expected outcome.
    """

    import json as _json

    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    if run_id is None:
        raise NoBlockProposalError(
            job_id, "the job carries no optimization_run_id for its placement"
        )

    event = _latest_placement_event(job_id, run_id, events)
    metadata = event.metadata

    block = job.get("block_candidate") or {}
    snapshot = _json.loads(run.provenance_snapshot_json or "{}")
    provenance = ProvenanceProfile.from_dict(snapshot["provenance"])

    explanation = _explanation_items(metadata)

    start = int(job["schedule_start_minute"])
    end = int(job["schedule_end_minute"])

    return BlockProposal(
        proposal_id=f"PROP-{run_id}-{job_id}",
        job_id=job_id,
        optimization_run_id=run_id,
        corridor_id=corridor_id,
        track_id=job["track_id"],
        section_id=block.get("section_id"),
        start_minute=start,
        end_minute=end,
        duration_minutes=end - start,
        work_type=job["work_type"],
        priority_score=float(job["priority_score"]),
        risk_score=float(job["risk_score"]),
        objective_score=float(
            metadata.get(
                "objective_score",
                job["priority_score"] + job["risk_score"],
            )
        ),
        is_committed=_derive_is_committed(job),
        explanation=explanation,
        provenance=provenance,
        generated_at=event.occurred_at,
    )


__all__ = [
    "BlockProposal",
    "NoBlockProposalError",
    "ProposalExplanationItem",
    "build_block_proposal",
]
