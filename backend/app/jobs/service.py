from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from contracts import DEFAULT_HORIZON_START, BlockCandidate, BlockStatus

from backend.app.data.canonical_train import (
    TrainDataProvenance,
    TrainDataSnapshot,
)

from backend.app.data.corridor_dataset import (
    CorridorDataset,
    load_corridor_dataset,
)

from backend.app.data.horizon_anchor import horizon_relative_minutes

from backend.app.data.feature_adapter import (
    ScoringFeatureAdapter,
    WORK_TYPE_DEFAULT_DURATIONS,
)

from backend.app.data.generator import (
    CorridorDataGenerator,
)

from backend.app.data.provenance import (
    ProvenanceLevel,
    from_train_data_provenance,
)

from backend.app.data.section_registry import SectionRegistry

from backend.app.data.timetable_coverage import (
    TimetableCoverage,
    horizon_calendar_dates,
    uncovered_horizon_days,
)

from backend.app.data.train_provider import (
    StaticTimetableProvider,
    possession_windows_from_provider,
)

from contracts import PossessionWindow

from backend.app.data.models import Corridor

from backend.app.identity.actor import (
    SCORER,
    Actor,
    unidentified_actor,
)

from backend.app.identity.authorization import (
    AuthorizationPolicy,
    JobAction,
    UnenforcedPolicy,
)

from backend.app.jobs.events import event_timestamp

from backend.app.jobs.execution import (
    EvidenceItem,
    ExecutionIntegrityError,
    ExecutionRecord,
    build_execution_records,
    new_execution_id,
)

from backend.app.jobs.history import (
    JobHistoryRepository,
    StoredJobEvent,
)

from backend.app.jobs.lifecycle import (
    COMMITTED_STATUSES,
    CommittedJobError,
    CommittedStateIntegrityError,
    InvalidTransitionError,
    Plan,
    StaleExecutionError,
    StaleProposalError,
    creation_events,
    plan_commit,
    plan_completion,
    plan_execution_complete,
    plan_execution_not_completed,
    plan_execution_start,
    plan_postpone,
    plan_reject,
    assert_committed_state_consistent,
    plan_schedule_assignment,
    proposal_run_id_of,
    rejected_transition_event,
)

from backend.app.jobs.models import (
    JobCreateRequest,
    JobStatus,
)

from backend.app.jobs.proposal import (
    BlockProposal,
    NoBlockProposalError,
    build_block_proposal,
)

from backend.app.jobs.repository import (
    JobRepository,
    TerminalJobError,
)

from backend.app.jobs.resource_resolution import (
    JobResourceResolutionError,
    resolve_job_resource,
)


DEFAULT_CORRIDOR_ID = os.getenv(
    "PASHUPAT_CORRIDOR_ID",
    "CORRIDOR_A",
)

# The corridors CorridorDataGenerator builds. These are abstract
# synthetic spans with no station topology and no timetable - see
# DEFAULT_POSSESSION_COMPATIBILITY. Any other corridor id is taken to
# mean the checked-in dataset corridor (backend.app.data.corridor_dataset).
GENERATED_CORRIDOR_IDS = frozenset(
    {
        "CORRIDOR_A",
        "CORRIDOR_B_DENSE",
        "CORRIDOR_C_DISRUPTED",
    }
)

# Sprint 2 possession windows come from the deterministic corridor
# generator, not from any live or real-time source. This label travels
# with every optimization response so the provenance is never
# misrepresented as live train data.
POSSESSION_SOURCE_GENERATED_STATIC = "GENERATED_STATIC"

# HOW a set of possession windows was computed (Sprint 3 Step 10).
#
# This is a DERIVATION label, not a provenance value - the same split
# backend.app.data.canonical_train already draws between
# TraversalBasis/TraversalDerivation (how a value was computed) and
# TrainDataProvenance (where the data came from). Both derivations below
# are SYNTHETIC on the provenance axis today, so possession_source alone
# cannot distinguish them; this label is what does. It must never be
# read as provenance - see backend.app.data.provenance.
#
# CANONICAL_TIMETABLE_DERIVED
#     Train-free gaps computed from canonical SectionTraversals, per
#     (track_id, section_id). This is the Sprint 3 Step 5/8 path and is
#     the default wherever the corridor has a timetable.
# GENERATED_STATIC_SLOTS
#     The three fixed operational slots (night block, midday, evening
#     off-peak) that CorridorDataGenerator emits. They model no train
#     movement whatsoever. See DEFAULT_POSSESSION_COMPATIBILITY below
#     for why this path still exists.
POSSESSION_DERIVATION_CANONICAL_TIMETABLE = "CANONICAL_TIMETABLE_DERIVED"
POSSESSION_DERIVATION_GENERATED_SLOTS = "GENERATED_STATIC_SLOTS"

# THE COMPATIBILITY BOUNDARY, stated once and explicitly.
#
# The canonical timetable path is the default possession source for any
# corridor that HAS a timetable. The generator-slot path above is not a
# fallback that may fire silently: it applies to exactly one situation -
# a corridor with no timetable at all - and the corridors in that
# situation are the abstract synthetic ones (CORRIDOR_A/B/C) built by
# CorridorDataGenerator.
#
# Those corridors cannot use the canonical path, and deliberately are
# not made to. Their topology is two abstract endpoints spanning the
# whole corridor (CorridorDataGenerator.generate_synthetic_stations),
# whose own docstring records that "a synthetic corridor has no station
# topology to subdivide it and inventing intermediate stations would
# fabricate railway geometry". Authoring train movements over that span
# would fabricate exactly the operational content that determines when
# maintenance may be scheduled. The honest answer is to keep those
# corridors on a clearly-labelled generated-slot source rather than to
# invent a timetable so the canonical path can appear to be universal.
DEFAULT_POSSESSION_COMPATIBILITY = (
    "Corridors with no timetable (the abstract synthetic corridors) use "
    "GENERATED_STATIC_SLOTS. Corridors with a timetable use "
    "CANONICAL_TIMETABLE_DERIVED."
)

# Job statuses whose work has been committed to field personnel and
# must therefore be preserved (pinned) through future optimization.
# Defined once in backend.app.jobs.lifecycle and imported above.

# The corridor generator, the shipped fixtures and BlockCandidate's own
# CONTRACT-LEVEL default latest_end_minute all use a 1440-minute (24h)
# planning day - see contracts/schemas.py's own pinned-fixture warning
# on that default, which this module never touches.
#
# Slice 4 Step 2: OPTIMIZATION_HORIZON_MINUTES is the DEFAULT deployment
# horizon width - it is still what a plain JobService() gets, and it is
# still imported directly by callers that want "the default value"
# without constructing a service (e.g. build_jobs_optimization_request's
# own default parameter). The actual per-deployment horizon width lives
# on JobService.horizon_minutes (set from this constant unless a caller
# overrides it explicitly), which is what every jobs-pipeline
# computation that used to read this module constant directly now reads
# instead - see JobService.__init__. This is deliberately the ONE knob:
# nothing here introduces a second, independent horizon configuration.
#
# Slice 4 Step 5: this value is now 2880 (two calendar days), not 1440.
# It moved from a coincidental single-day value to a deliberately chosen
# multi-day production default - see SLICE4_MULTIDAY_SCHEDULING_DESIGN.md
# Sec.4.3/15 and the Step 5 load-test evidence recorded in the Step 5
# PR/report. This is safe for a corridor on the CANONICAL timetable-
# backed possession path (a corridor with timetable_records) ONLY
# because Slice 4 Step 4's timetable coverage gate
# (JobService._canonical_possession_inputs) already fails closed on any
# calendar date this horizon touches that the canonical timetable does
# not assert data for - any horizon wider than a timetable's covered
# service_dates fails closed (TimetableCoverageGapError), by design,
# rather than silently deriving possession for a day nobody described.
# (Since Slice 4 Step 7 the checked-in synthetic dataset covers
# 2026-09-10 and 2026-09-11, so it passes at this default and fails
# closed at 4320.) The DEFAULT deployment corridor (CORRIDOR_A, unless
# PASHUPAT_CORRIDOR_ID selects the checked-in CORR-NDLS-AGC dataset) has
# no timetable_records at all, so it never reaches this gate: it takes
# the GENERATED synthetic-slots path instead
# (JobService._generated_possession_inputs), which is safe for a
# different reason - repeat_daily_slots invents its slots outright, so
# there is no absent timetable data to misread as free track time in
# the first place. Widening the horizon default is therefore safe on
# BOTH paths, for two different, module-documented reasons - not because
# this one gate is universal. 4320 and 10080 (3/7 days) were
# both measured and rejected for this default: CP-SAT solve time grows
# steeply with (horizon days x active job count) on this solver's fixed
# 10-second budget (solver.py's _SOLVE_TIME_LIMIT_SECONDS, unchanged),
# and both already produced FEASIBLE-not-OPTIMAL (timed-out) solves at
# job counts only ~2x the checked-in "realistic" fixture's own 15
# concurrent jobs. 2880 stayed OPTIMAL well past that same margin. See
# the Step 5 report for the exact measurements. BlockCandidate's
# contract-level default (1440) and the corridor generator/fixture
# constant referenced above are UNCHANGED and are not read from here.
OPTIMIZATION_HORIZON_START = DEFAULT_HORIZON_START
OPTIMIZATION_HORIZON_MINUTES = 2880
DEFAULT_MIN_HEADWAY_MINUTES = 15


class InvalidHorizonMinutesError(ValueError):
    """A JobService was asked to use a deployment horizon that is not a
    positive whole number of calendar days.

    Jobs-pipeline policy only (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md
    Sec.4.3), not a contract-level rule: contracts.OptimizationRequest.
    horizon_minutes itself accepts any positive int (see
    test_horizon_midnight_crossing.py's 2880-minute cases). This
    deployment chooses to keep "postpone to local midnight of a date"
    meaningful by only ever running whole calendar days, so the
    JobService construction site - not the solver or the contract -
    is where that choice is enforced.
    """


def derived_possession_provenance(
    timetable_provenance: ProvenanceLevel,
) -> ProvenanceLevel:
    """Provenance of possession windows DERIVED from a timetable.

    Two Step 9 rules apply at once, and only their intersection is
    settled today:

      1. a derived window can never be MORE real than the timetable it
         was derived from; and
      2. a derived value does not automatically become as real as its
         inputs merely because those inputs are real - the window is a
         computed artifact, not something the source stated.

    Rule 1 alone would let a REAL_SCHEDULED timetable yield
    REAL_SCHEDULED possession. Rule 2 says that promotion is exactly
    what must not happen automatically. That decision is genuinely open,
    so it is left OPEN here: only the SYNTHETIC case (the only one this
    repository can currently produce) returns a value, and anything else
    fails closed rather than being resolved by guessing. See
    backend.app.data.provenance.to_possession_source, which fails closed
    on the same boundary for the legacy compatibility label.
    """

    if timetable_provenance is ProvenanceLevel.SYNTHETIC:
        return ProvenanceLevel.SYNTHETIC

    raise ValueError(
        "Possession windows derived from a "
        f"{timetable_provenance.value} timetable have no settled "
        "provenance yet: a derived window must not be silently promoted "
        "to the realness of its inputs. Settle that rule before "
        "connecting a non-synthetic timetable."
    )


class TimetableCoverageGapError(RuntimeError):
    """The canonical timetable does not cover every calendar date the
    configured optimization horizon requires (Slice 4 Step 4).

    Distinct from PossessionDataUnavailableError (backend.app.jobs.
    optimization), which means NO possession data exists at all: this
    means the canonical timetable DOES carry some data, but not for
    every day the horizon spans. Reused verbatim from Step 1's isolated
    capability (backend.app.data.timetable_coverage) - this class is the
    only new code the wiring needed; covered_service_dates,
    horizon_calendar_dates and uncovered_horizon_days are unchanged.

    WHY THE WHOLE ATTEMPT IS REFUSED, NOT JUST THE UNCOVERED DAYS
        Missing timetable data must never be interpreted as an absence
        of trains - see the timetable_coverage module docstring for why
        "covered" only ever asserts presence, never completeness. This
        error is therefore raised BEFORE any possession window is
        derived from this timetable at all (backend.app.data.
        train_provider.possession_windows_from_provider is never
        called), so no window - not even one for an already-covered
        day - is derived from a request that, as a whole, cannot be
        trusted. This mirrors PossessionDataUnavailableError's own
        existing all-or-nothing refusal for the "no possession data at
        all" case; a per-day partial derivation is a different, larger
        design decision this step does not make.

    Caught by JobOptimizationService.optimize_corridor's existing
    `except Exception` around the possession_inputs() call - the exact
    same generic catch that already handles PossessionDataUnavailableError
    today, so this failure gets the SAME existing lifecycle treatment
    with no new code in optimization.py: no solver run, no
    optimization_runs audit row, any job that was 'scheduled' has its
    stale proposal withdrawn (OPTIMIZATION_FAILED / PROPOSAL_INVALIDATED),
    and a job with no current proposal (e.g. one just postponed) is left
    exactly as it was.
    """

    def __init__(
        self,
        corridor_id: str,
        horizon_start: str,
        horizon_minutes: int,
        required_dates,
        covered_dates,
        uncovered_dates,
    ):
        self.corridor_id = corridor_id
        self.horizon_start = horizon_start
        self.horizon_minutes = horizon_minutes
        self.required_dates = tuple(required_dates)
        self.covered_dates = tuple(covered_dates)
        self.uncovered_dates = tuple(uncovered_dates)

        required_str = ", ".join(d.isoformat() for d in self.required_dates)
        covered_str = (
            ", ".join(d.isoformat() for d in self.covered_dates)
            if self.covered_dates
            else "none"
        )
        uncovered_str = ", ".join(d.isoformat() for d in self.uncovered_dates)

        message = (
            f"Timetable coverage for corridor {corridor_id!r} does not "
            "include every calendar date the configured optimization "
            f"horizon requires (horizon_start={horizon_start!r}, "
            f"horizon_minutes={horizon_minutes}). Required dates: "
            f"[{required_str}]. Covered dates: [{covered_str}]. "
            f"Uncovered dates: [{uncovered_str}]. Refusing to optimize: "
            "missing timetable data must never be interpreted as free "
            "track capacity, so no possession window was derived for "
            "any date in this request."
        )

        super().__init__(message)


@dataclass(frozen=True)
class PossessionInputs:
    """One resolved set of possession windows, and how it was obtained.

    Carries the provenance axes the windows imply so
    backend.app.jobs.optimization can build ONE ProvenanceProfile from
    the inputs actually used, rather than restating provenance
    independently where the two could disagree.

    snapshot is present only on the canonical path. It is the record of
    which trains were REFUSED - possession derivation withholds every
    section a rejected train might have occupied, so a caller that
    discards it loses the only explanation for a missing maintenance
    opportunity.
    """

    windows: list[PossessionWindow]
    derivation: str
    timetable_provenance: ProvenanceLevel
    possession_provenance: ProvenanceLevel
    snapshot: Optional[TrainDataSnapshot] = None

    @property
    def rejections(self) -> tuple:
        return () if self.snapshot is None else self.snapshot.rejections


class JobService:
    """Maintenance job reporting, lifecycle and optimization input.

    ACTOR CONTEXT (Slice 1)
        Every state-changing method takes `actor`. It is optional only
        so pre-Slice-1 in-process callers keep working: an omitted actor
        is recorded as the explicit UNIDENTIFIED actor
        (backend.app.identity.actor.unidentified_actor), never as an
        invented person. The HTTP layer always passes the actor it
        resolved from the request. Automated work inside a human action
        (scoring during a report) is recorded under a SYSTEM actor.

    AUTHORIZATION SEAM
        Each method calls self.authorization.authorize(actor, action)
        before reading or writing anything. The default policy enforces
        nothing - see backend.app.identity.authorization.

    LIFECYCLE LOCK
        lifecycle_lock serializes every transition that can change an
        existing job - optimization (JobOptimizationService acquires this
        same lock), commit, completion and schedule assignment - so an
        optimization cannot rewrite a proposal while it is being
        committed, and a commit cannot land between an optimization's
        snapshot and its write. It is an in-process threading.RLock:
        it protects nothing across processes. The cross-process guard is
        the database itself (BEGIN IMMEDIATE transactions plus the
        snapshot check in lifecycle.plan_optimization_outcome), which
        fails closed with ConcurrentJobModificationError. Report intake
        (create_job) does not take the lock: a new job cannot conflict
        with a transition on an existing one.

    DEPLOYMENT HORIZON (Slice 4 Step 2, widened Slice 4 Step 5)
        self.horizon_minutes is the ONE source of "how many minutes wide
        is this deployment's planning horizon" for everything the jobs
        pipeline does - defaults to OPTIMIZATION_HORIZON_MINUTES (2880,
        i.e. two calendar days as of Slice 4 Step 5; see that constant's
        own comment for why). It governs both a NEW job's admissible
        window (create_job's BlockCandidate.latest_end_minute) and the
        possession window boundary (possession_inputs/possession_windows'
        own horizon_minutes default) - the same two hardcoded values the
        Slice 4 design audit found independently agreeing by coincidence
        are now the same one instance attribute. Constructing
        JobService(horizon_minutes=4320) widens both at once; nothing
        else needs to change to stay consistent. This is deliberately
        the only horizon-width knob - JobOptimizationService reads this
        same attribute (via its own JobService) rather than keeping a
        second value.

        Safe only in combination with the Step 4 timetable coverage gate
        (_canonical_possession_inputs): a wider horizon over a timetable
        that does not describe every day it now touches fails closed
        (TimetableCoverageGapError) rather than silently manufacturing
        possession from missing data. The checked-in synthetic dataset
        covers two service_dates (Slice 4 Step 7), so it passes at the
        2880 default and fails closed at 4320 - see
        test_slice4_step7_two_day_demo_dataset.py.

        Still jobs-pipeline policy only: contracts.OptimizationRequest.
        horizon_minutes (the solver-facing contract) is unconstrained,
        and BlockCandidate.latest_end_minute's contract-level DEFAULT
        stays 1440 exactly as pinned by contracts/schemas.py - this
        attribute never touches either.
    """

    def __init__(
        self,
        repository: JobRepository | None = None,
        corridor: Corridor | None = None,
        dataset: CorridorDataset | None = None,
        registry: SectionRegistry | None = None,
        authorization: AuthorizationPolicy | None = None,
        horizon_minutes: int = OPTIMIZATION_HORIZON_MINUTES,
        clock: Callable[[], datetime] | None = None,
    ):
        if horizon_minutes <= 0 or horizon_minutes % 1440 != 0:
            raise InvalidHorizonMinutesError(
                "JobService.horizon_minutes must be a positive whole "
                f"number of calendar days (a positive multiple of 1440); "
                f"got {horizon_minutes}. See SLICE4_MULTIDAY_SCHEDULING_"
                "DESIGN.md Sec.4.3."
            )

        self.horizon_minutes = horizon_minutes

        # Sprint 3 Slice 5: the one source of "now" for field-execution
        # observation-time rules (recording moment for EXECUTION_* events;
        # see plan_execution_start/_complete/_not_completed's own future-
        # skew checks). Injectable so tests are clock-independent; every
        # other JobService construction site keeps working unchanged.
        self.clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self.repository = (
            repository or JobRepository()
        )

        # Pinned to this repository's own file, exactly as
        # JobOptimizationService pins its AuditRepository, so an
        # isolated test database keeps its history isolated too.
        self.history = JobHistoryRepository(self.repository.db_path)

        self.authorization: AuthorizationPolicy = (
            authorization or UnenforcedPolicy()
        )

        self.lifecycle_lock = threading.RLock()

        # A dataset corridor carries its own topology, section registry
        # and timetable; a generated corridor carries none of those, and
        # self.dataset stays None. That single attribute is what
        # possession_inputs branches on - see
        # DEFAULT_POSSESSION_COMPATIBILITY.
        if dataset is None and corridor is None:
            dataset = self._load_default_dataset()

        self.dataset = dataset

        if dataset is not None:
            self.corridor = corridor or dataset.corridor
            self.registry = registry or dataset.registry
        elif corridor is not None:
            # An explicit corridor with no dataset and no registry: no
            # existing caller does this (every current construction site
            # passes either `dataset=` or neither), but nothing prevents
            # a future one from doing so. self.registry stays None
            # rather than being guessed at - create_job fails closed
            # with an explicit reason (Sprint 3 Step 11) rather than
            # silently producing an unresolved job. See
            # test_job_service_without_a_registry_fails_closed_on_create.
            self.corridor = corridor
            self.registry = registry
        else:
            self.corridor, self.registry = (
                self._build_default_corridor_and_registry()
            )

    @staticmethod
    def _load_default_dataset() -> CorridorDataset | None:
        """The checked-in dataset, when this deployment's corridor IS it.

        DEFAULT_CORRIDOR_ID selects the corridor. The three generated
        corridor ids are handled by _build_default_corridor and have no
        dataset; any other id is taken to mean the checked-in dataset
        corridor, and is verified against the dataset's own declared
        corridor_id rather than assumed - a typo must fail loudly, not
        silently serve a different corridor.
        """

        if DEFAULT_CORRIDOR_ID in GENERATED_CORRIDOR_IDS:
            return None

        dataset = load_corridor_dataset()

        if dataset.corridor_id != DEFAULT_CORRIDOR_ID:
            raise ValueError(
                f"PASHUPAT_CORRIDOR_ID is {DEFAULT_CORRIDOR_ID!r}, which "
                "is neither a generated corridor "
                f"({sorted(GENERATED_CORRIDOR_IDS)}) nor the checked-in "
                f"dataset corridor ({dataset.corridor_id!r})."
            )

        return dataset

    @staticmethod
    def _build_default_corridor_and_registry() -> tuple[
        Corridor,
        SectionRegistry,
    ]:
        """A generated corridor AND the SectionRegistry for its span.

        Sprint 3 Step 11: every generated corridor previously had tracks
        and assets but no registry, so no job created against it could
        resolve a section_id at all. build_section_registry uses the
        SAME generate_synthetic_stations construction site the
        generator's own scenario builders (generate_scenario_a/b/c) use
        for their fixtures, so a job resolved here and a candidate block
        resolved by generate_candidate_blocks land on the identical
        section_id - there is exactly one section per generated
        corridor (generate_synthetic_stations emits exactly two abstract
        endpoints; see that method's docstring for why intermediate
        stations are never fabricated), so resolution is trivial but not
        hand-waved: it goes through the same SectionRegistry lookup a
        multi-section corridor would use.
        """

        generator = CorridorDataGenerator(
            seed=42
        )

        corridor_id = DEFAULT_CORRIDOR_ID

        if corridor_id == "CORRIDOR_B_DENSE":
            corridor_length_km = 60.0
            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=4,
                corridor_length_km=corridor_length_km,
            )

        elif corridor_id == "CORRIDOR_C_DISRUPTED":
            corridor_length_km = 40.0
            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=2,
                corridor_length_km=corridor_length_km,
            )

        else:
            corridor_length_km = 35.0
            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=2,
                corridor_length_km=corridor_length_km,
            )

        assets = generator.generate_assets(
            tracks,
            num_assets_per_track=8,
        )

        registry = generator.build_section_registry(
            corridor_id,
            tracks,
            corridor_length_km,
        )

        corridor = Corridor(
            corridor_id=corridor_id,
            name=corridor_id.replace(
                "_",
                " ",
            ).title(),
            tracks=tracks,
            assets=assets,
        )

        return corridor, registry

    def create_job(
        self,
        request: JobCreateRequest,
        actor: Actor | None = None,
    ) -> dict[str, Any]:

        reporter = self._resolve_actor(actor)
        self.authorization.authorize(reporter, JobAction.REPORT_JOB)

        # ---------------------------------
        # 0. Validate corridor, when named
        # ---------------------------------
        #
        # This deployment serves exactly one corridor (self.corridor).
        # corridor_id is optional precisely so it never becomes a
        # routing mechanism a caller silently depends on; when given, it
        # is checked, not used to select anything, so a caller that
        # names the wrong corridor fails closed instead of having its
        # job filed against a corridor it did not intend.
        if (
            request.corridor_id is not None
            and request.corridor_id != self.corridor.corridor_id
        ):
            raise ValueError(
                f"corridor_id {request.corridor_id!r} does not match this "
                f"deployment's corridor {self.corridor.corridor_id!r}"
            )

        # ---------------------------------
        # 1. Validate track
        # ---------------------------------

        track = next(
            (
                t
                for t in self.corridor.tracks
                if t.track_id == request.track_id
            ),
            None,
        )

        if track is None:
            raise ValueError(
                f"Unknown track_id '{request.track_id}'"
            )

        # ---------------------------------
        # 2. Use existing duration model
        # ---------------------------------

        duration = WORK_TYPE_DEFAULT_DURATIONS.get(
            request.job_type.value
        )

        if duration is None:
            raise ValueError(
                "No duration model configured for "
                f"work type '{request.job_type.value}'"
            )

        # ---------------------------------
        # 3. Find nearest existing asset
        # ---------------------------------

        asset = self._nearest_asset(
            request.track_id,
            request.distance_start,
            request.distance_end,
        )

        if asset is None:
            raise ValueError(
                f"No asset found for "
                f"track_id '{request.track_id}'"
            )

        # ---------------------------------
        # 3.5. Resolve the canonical (track_id, section_id) resource
        # ---------------------------------
        #
        # Sprint 3 Step 11. This is the earliest point corridor, asset
        # location and track assignment are all known, so it is the
        # ONE place section_id is derived - never re-derived downstream.
        # See backend.app.jobs.resource_resolution.resolve_job_resource
        # for the fail-closed contract this enforces: the job's declared
        # distance_start/distance_end must resolve, together, to exactly
        # one registered section on this track, or the job is refused
        # outright rather than persisted with an ambiguous or absent
        # section_id.
        #
        # self.registry is only ever None for a JobService constructed
        # with an explicit corridor= and no registry= - no current
        # caller does this - so the AttributeError-shaped failure here
        # is deliberate: a caller in that situation must supply a
        # registry, not have one silently skipped.
        if self.registry is None:
            raise ValueError(
                "This JobService has no SectionRegistry, so job "
                f"locations cannot be safely resolved to a section. "
                f"Corridor {self.corridor.corridor_id!r} was "
                "constructed without one - pass registry= or dataset= "
                "when building JobService."
            )

        try:
            resource = resolve_job_resource(
                self.registry,
                request.track_id,
                request.distance_start,
                request.distance_end,
            )
        except JobResourceResolutionError as exc:
            raise ValueError(str(exc)) from exc

        # ---------------------------------
        # 4. Create canonical BlockCandidate
        # ---------------------------------

        block = BlockCandidate(
            block_id=(
                f"JOB-"
                f"{uuid.uuid4().hex[:10].upper()}"
            ),

            asset_id=asset.asset_id,

            track_id=resource.track_id,

            section_id=resource.section_id,

            work_type=request.job_type.value,

            duration_minutes=duration,

            earliest_start_minute=0,

            latest_end_minute=self.horizon_minutes,

            priority_score=0.0,

            risk_score=0.0,

            dependencies=[],

            mutual_exclusion_group=None,

            is_committed=False,

            status=BlockStatus.PLANNED.value,

            metadata={
                "source": "worker_report",

                "job_type":
                    request.job_type.value,

                "distance_start":
                    request.distance_start,

                "distance_end":
                    request.distance_end,

                "workers_min":
                    request.workers_min,

                "workers_max":
                    request.workers_max,

                "description":
                    request.description,

                "asset_name":
                    asset.name,

                "asset_km_location":
                    asset.km_location,

                # Sprint 3 Slice 2: the worker's own report, carried
                # verbatim in metadata rather than as new BlockCandidate
                # fields or SQL columns - see JobCreateRequest.severity/
                # evidence_reference. None when the worker did not
                # supply one.
                "reported_severity":
                    request.severity.value if request.severity else None,

                "evidence_reference":
                    request.evidence_reference,
            },
        )

        # ---------------------------------
        # 5. Use EXISTING scorer
        # ---------------------------------
        #
        # A worker-reported severity overrides the asset's own stored
        # defect_severity for THIS job's score - see
        # ScoringFeatureAdapter.score_block_candidate. Omitted (None)
        # keeps the pre-Slice-2 behaviour of scoring off the asset's
        # record.

        scored_block = (
            ScoringFeatureAdapter.score_block_candidate(
                block,
                self.corridor,
                explicit_defect_severity=(
                    request.severity.value if request.severity else None
                ),
            )
        )

        # ---------------------------------
        # 6. Store in DB, with its creation history
        # ---------------------------------

        created_at = event_timestamp()

        job = {
            "job_id":
                scored_block.block_id,

            "track_id":
                request.track_id,

            "work_type":
                request.job_type.value,

            "distance_start":
                request.distance_start,

            "distance_end":
                request.distance_end,

            "workers_min":
                request.workers_min,

            "workers_max":
                request.workers_max,

            "description":
                request.description,

            "status":
                JobStatus.REPORTED.value,

            "priority_score":
                scored_block.priority_score,

            "risk_score":
                scored_block.risk_score,

            "schedule_start_minute":
                None,

            "schedule_end_minute":
                None,

            "created_at":
                created_at,

            "updated_at":
                created_at,

            "block_candidate":
                scored_block.to_dict(),
        }

        return self.repository.create(
            job,
            creation_events(
                job,
                reporter=reporter,
                scorer=SCORER,
                at=created_at,
            ),
        )

    # -----------------------------------------
    # GET /jobs support
    # -----------------------------------------

    def list_jobs(self) -> list[dict[str, Any]]:

        return self.repository.list_all()

    # -----------------------------------------
    # Lifecycle history
    # -----------------------------------------

    def job_history(
        self,
        job_id: str,
        actor: Actor | None = None,
    ) -> list[StoredJobEvent]:
        """Every lifecycle event for one job, in commit order.

        KeyError if the job does not exist. Read-only: history has no
        update or delete operation anywhere in the application.
        """

        reader = self._resolve_actor(actor)
        self.authorization.authorize(reader, JobAction.READ_JOB_HISTORY)

        if self.repository.get(job_id) is None:
            raise KeyError(f"Job '{job_id}' not found")

        return self.history.list_for_job(job_id)

    # -----------------------------------------
    # Current BlockProposal (Sprint 3 Slice 2)
    # -----------------------------------------

    def current_proposal(
        self,
        job_id: str,
        actor: Actor | None = None,
    ) -> BlockProposal:
        """The job's current NEW-scheduling proposal, or an explicit refusal.

        KeyError if the job does not exist. NoBlockProposalError - never
        a fabricated proposal - if the job exists but has none right
        now: never yet optimized, considered and left UNSCHEDULED, or
        already committed/completed (a proposal is not a commitment; see
        backend.app.jobs.proposal).

        Derives the proposal from this job's own history and the
        optimization_runs record for the run that produced it - see
        backend.app.jobs.proposal.build_block_proposal. The audit
        repository is pinned to THIS service's own repository.db_path,
        exactly as JobOptimizationService pins it, so an isolated test
        database's proposals are built from that same isolated file.
        """

        reader = self._resolve_actor(actor)
        self.authorization.authorize(reader, JobAction.READ_BLOCK_PROPOSAL)

        return self._build_current_proposal(job_id)

    def _build_current_proposal(self, job_id: str) -> BlockProposal:
        """The build logic behind current_proposal, with no authorization call.

        Split out so approve_proposal/reject_proposal/postpone_proposal
        (Sprint 3 Slice 3) can obtain the SAME derived proposal - e.g. to
        compute BlockProposal.digest() for audit metadata - under their
        OWN action's authorization check, without a second
        READ_BLOCK_PROPOSAL authorization firing for one caller-visible
        action. See current_proposal for what each exception means.
        """

        job = self.repository.get(job_id)

        if job is None:
            raise KeyError(f"Job '{job_id}' not found")

        if job["status"] != JobStatus.SCHEDULED.value:
            raise NoBlockProposalError(job_id, _no_proposal_reason(job))

        run_id = proposal_run_id_of(job)

        if run_id is None:
            raise NoBlockProposalError(
                job_id,
                "job is 'scheduled' but carries no optimization_run_id; "
                "this is a stored-state inconsistency",
            )

        # Local import: backend.app.audit.repository imports
        # backend.app.jobs.repository (for DEFAULT_DB_PATH), and
        # backend/app/jobs/__init__.py eagerly imports this module - a
        # module-level import here would import audit.repository while
        # it is still mid-import-of-jobs, a circular import. Every other
        # in-process caller of AuditRepository (JobOptimizationService)
        # sits in backend.app.jobs.optimization, a separate module the
        # package __init__ does not eagerly import, so it does not hit
        # this cycle.
        from backend.app.audit.repository import AuditRepository

        run = AuditRepository(self.repository.db_path).get(run_id)

        if run is None:
            raise NoBlockProposalError(
                job_id,
                f"optimization run {run_id!r} referenced by this job's "
                "proposal was not found in the audit trail",
            )

        events = self.history.list_for_job(job_id)

        return build_block_proposal(job, self.corridor.corridor_id, events, run)

    def _current_proposal_digest(self, job_id: str) -> Optional[str]:
        """Best-effort BlockProposal.digest() for audit metadata only.

        Never raises and never blocks a transition: the digest is
        traceability, not the safety mechanism (that is
        expected_proposal_run_id, checked inside the transactional
        mutation itself). If the proposal cannot be rebuilt right now -
        including a benign race against a concurrent writer - the
        transition proceeds and simply records no digest.
        """

        try:
            return self._build_current_proposal(job_id).digest()
        except (KeyError, NoBlockProposalError):
            return None

    # -----------------------------------------
    # scheduled -> notified
    # -----------------------------------------

    def notify(
        self,
        job_id: str,
        actor: Actor | None = None,
        expected_proposal_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit (pin) the job's CURRENT proposal.

        Notifying is what makes the work operationally committed: the
        persisted BlockCandidate becomes COMMITTED so a later
        optimization reconstructs it as an existing committed block and
        the solver preserves its placement.

        expected_proposal_run_id, when given, must equal the run that
        produced the current proposal; otherwise StaleProposalError, so
        a commit can never land on a proposal other than the one the
        caller reviewed. Omitting it commits whatever proposal is current
        at the moment the lifecycle lock is held - which, since Slice 1,
        is always the latest optimization's proposal.
        """

        committer = self._resolve_actor(actor)
        self.authorization.authorize(committer, JobAction.COMMIT_BLOCK)

        return self._transition(
            job_id,
            committer,
            "notify",
            plan_commit(
                job_id,
                actor=committer,
                at=event_timestamp(),
                expected_proposal_run_id=expected_proposal_run_id,
            ),
        )

    # -----------------------------------------
    # Authority review of a NEW block proposal (Sprint 3 Slice 3)
    # -----------------------------------------

    def approve_proposal(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        expected_proposal_run_id: str,
    ) -> dict[str, Any]:
        """Approve the job's CURRENT proposal: scheduled -> notified.

        There is deliberately no second commit implementation here:
        this delegates straight to notify(), the existing commit
        machinery, which already requires JobAction.COMMIT_BLOCK - not
        a separate APPROVE_PROPOSAL permission - and already records
        BLOCK_COMMITTED. The only difference from calling notify()
        directly is that expected_proposal_run_id is MANDATORY here
        (notify's own body makes it optional, for compatibility), so an
        approval can never silently commit "whatever is current".
        """

        if not expected_proposal_run_id or not expected_proposal_run_id.strip():
            raise ValueError(
                "expected_proposal_run_id is required to approve a proposal"
            )

        return self.notify(
            job_id,
            actor=actor,
            expected_proposal_run_id=expected_proposal_run_id,
        )

    def reject_proposal(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        expected_proposal_run_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Reject the job's CURRENT proposal outright: scheduled -> reported.

        expected_proposal_run_id and reason are both mandatory: a
        rejection decision must name the exact proposal it refuses and
        why. The job returns to 'reported' with reason recorded as
        last_refusal_reason (see backend.app.jobs.lifecycle.plan_reject)
        so it reads identically to a solver-side refusal, and remains
        eligible for the next optimization attempt to produce a
        genuinely new proposal.
        """

        rejecter = self._resolve_actor(actor)
        self.authorization.authorize(rejecter, JobAction.REJECT_PROPOSAL)

        if not expected_proposal_run_id or not expected_proposal_run_id.strip():
            raise ValueError(
                "expected_proposal_run_id is required to reject a proposal"
            )

        if not reason or not reason.strip():
            raise ValueError("reason is required to reject a proposal")

        return self._transition(
            job_id,
            rejecter,
            "reject",
            plan_reject(
                job_id,
                actor=rejecter,
                at=event_timestamp(),
                reason=reason,
                expected_proposal_run_id=expected_proposal_run_id,
            ),
        )

    def postpone_proposal(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        expected_proposal_run_id: str,
        reason: str,
        selected_date: str,
        horizon_start: str = OPTIMIZATION_HORIZON_START,
    ) -> dict[str, Any]:
        """Postpone the job's CURRENT proposal: scheduled -> reported.

        selected_date is converted to a not-before minute through
        horizon_relative_minutes - the one authoritative horizon-
        anchoring conversion (backend.app.data.horizon_anchor) - using
        the SAME horizon_start this deployment's optimizer anchors
        every run to (OPTIMIZATION_HORIZON_START), so the
        earliest_start_minute written onto the job's block is in the
        exact coordinate frame the next optimization run and its solver
        will read it in. horizon_start is overridable only for tests
        that need to prove the conversion itself; no production caller
        passes it.

        Fails closed (InvalidTransitionError, from
        backend.app.jobs.lifecycle.plan_postpone) if the converted
        minute falls outside [0, self.horizon_minutes) - THIS instance's
        own deployment horizon (Slice 4 Step 3), not the job's own
        stored latest_end_minute - past or beyond - rather than silently
        creating availability the optimizer was never meant to offer.

        WIDENING (Slice 4 Step 3): a valid postponement also raises the
        job's stored latest_end_minute to
        max(current latest_end_minute, self.horizon_minutes) - see
        plan_postpone's own docstring for why. This is what keeps a
        postponed job genuinely schedulable by the next optimization
        run instead of silently becoming window_infeasible.
        """

        postponer = self._resolve_actor(actor)
        self.authorization.authorize(postponer, JobAction.POSTPONE_PROPOSAL)

        if not expected_proposal_run_id or not expected_proposal_run_id.strip():
            raise ValueError(
                "expected_proposal_run_id is required to postpone a proposal"
            )

        if not reason or not reason.strip():
            raise ValueError("reason is required to postpone a proposal")

        not_before_minute = horizon_relative_minutes(
            selected_date, 0, 0, horizon_start
        )

        digest = self._current_proposal_digest(job_id)

        return self._transition(
            job_id,
            postponer,
            "postpone",
            plan_postpone(
                job_id,
                actor=postponer,
                at=event_timestamp(),
                reason=reason,
                selected_date=selected_date,
                not_before_minute=not_before_minute,
                horizon_minutes=self.horizon_minutes,
                expected_proposal_run_id=expected_proposal_run_id,
                proposal_digest=digest,
            ),
        )

    # -----------------------------------------
    # notified -> completed
    # -----------------------------------------

    def complete(
        self,
        job_id: str,
        actor: Actor | None = None,
    ) -> dict[str, Any]:

        completer = self._resolve_actor(actor)
        self.authorization.authorize(completer, JobAction.COMPLETE_JOB)

        return self._transition(
            job_id,
            completer,
            "complete",
            plan_completion(job_id, actor=completer, at=event_timestamp()),
        )

    # -----------------------------------------
    # Field execution (Sprint 3 Slice 5 Step 3)
    #
    # Each method is a thin wrapper: authorize, pre-read the job's own
    # execution history under lifecycle_lock (build_execution_records -
    # I/O, so it cannot happen inside mutate_jobs' transaction), then
    # delegate the transition itself to the already-reviewed lifecycle
    # plan via _transition. The plan is the only place that decides
    # whether a transition is allowed; nothing here duplicates that
    # logic. The pre-read and the write share one lifecycle_lock
    # acquisition (RLock, so _transition's own acquisition nests) so the
    # execution record returned alongside the job is never stale against
    # a transition that landed in between.
    # -----------------------------------------

    def _reconstructed_execution(
        self,
        records: Sequence[ExecutionRecord],
        job_id: str,
        execution_id: str,
    ) -> ExecutionRecord:
        """The just-committed execution, or a fail-closed integrity error.

        The transition immediately before this call committed
        execution_id; if it cannot be found among the freshly rebuilt
        records, the write and the reconstruction disagree with each
        other, which is an ExecutionIntegrityError, not a StopIteration
        leaking out of a bare next().
        """

        for record in records:
            if record.execution_id == execution_id:
                return record

        raise ExecutionIntegrityError(
            f"Job '{job_id}' committed a transition for execution "
            f"{execution_id!r}, but that execution could not be found in "
            "the history immediately reconstructed afterward",
            job_id,
        )

    def _evidence_items(self, raw: Sequence[Mapping[str, Any]]) -> list[EvidenceItem]:
        """Construct+validate EvidenceItem values from plain evidence dicts.

        Raises EvidenceItem's own EvidenceValidationError (a ValueError)
        for a structurally invalid item - the router's generic ValueError
        handler maps that to 400, exactly as every other domain
        validation failure in this service already does.
        """

        return [EvidenceItem(**dict(item)) for item in raw]

    def start_execution(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        expected_proposal_run_id: str,
        actual_start_at: str,
        before_work_evidence: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ExecutionRecord]:
        """notified -> in_progress: field work on the approved block has started.

        Returns (job, execution) so the caller can hand the minted
        execution_id back to the field crew - it is never guessable, and
        Slice 5 has no idempotency key to recover it otherwise.
        """

        starter = self._resolve_actor(actor)
        self.authorization.authorize(starter, JobAction.START_EXECUTION)

        evidence = self._evidence_items(before_work_evidence)

        with self.lifecycle_lock:
            job = self.repository.get(job_id)

            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            previous = tuple(
                build_execution_records(job, self.history.list_for_job(job_id))
            )
            execution_id = new_execution_id()

            updated = self._transition(
                job_id,
                starter,
                "start_execution",
                plan_execution_start(
                    job_id,
                    actor=starter,
                    at=event_timestamp(self.clock()),
                    execution_id=execution_id,
                    expected_proposal_run_id=expected_proposal_run_id,
                    actual_start_at=actual_start_at,
                    before_work_evidence=evidence,
                    corridor_id=self.corridor.corridor_id,
                    previous_executions=previous,
                    horizon_start=OPTIMIZATION_HORIZON_START,
                ),
            )

            records = build_execution_records(
                updated, self.history.list_for_job(job_id)
            )
            execution = self._reconstructed_execution(records, job_id, execution_id)

        return updated, execution

    def _current_open_execution(
        self, job: Mapping[str, Any], job_id: str
    ) -> Optional[ExecutionRecord]:
        """The job's currently open execution, or None.

        None here means either "not in_progress" or "no open execution",
        which the plan itself distinguishes and refuses appropriately -
        see plan_execution_complete/_not_completed's shared
        _open_execution_checks. It is never reached with job.status ==
        in_progress and no matching open record: build_execution_records
        already refuses that as ExecutionIntegrityError while reading.
        """

        records = build_execution_records(job, self.history.list_for_job(job_id))
        open_records = [record for record in records if record.is_open]
        return open_records[0] if open_records else None

    def complete_execution(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        execution_id: str,
        actual_end_at: str,
        after_work_evidence: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ExecutionRecord]:
        """in_progress -> completed: field work finished, with after-work evidence.

        Terminal: the job never returns to optimization after this.
        """

        completer = self._resolve_actor(actor)
        self.authorization.authorize(completer, JobAction.COMPLETE_JOB)

        evidence = self._evidence_items(after_work_evidence)

        with self.lifecycle_lock:
            job = self.repository.get(job_id)

            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            open_execution = self._current_open_execution(job, job_id)

            updated = self._transition(
                job_id,
                completer,
                "complete_execution",
                plan_execution_complete(
                    job_id,
                    actor=completer,
                    at=event_timestamp(self.clock()),
                    execution_id=execution_id,
                    actual_end_at=actual_end_at,
                    after_work_evidence=evidence,
                    execution=open_execution,
                    corridor_id=self.corridor.corridor_id,
                    horizon_start=OPTIMIZATION_HORIZON_START,
                ),
            )

            records = build_execution_records(
                updated, self.history.list_for_job(job_id)
            )
            execution = self._reconstructed_execution(records, job_id, execution_id)

        return updated, execution

    def report_execution_not_completed(
        self,
        job_id: str,
        actor: Actor | None = None,
        *,
        execution_id: str,
        actual_end_at: str,
        reason: str,
        failure_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[dict[str, Any], ExecutionRecord]:
        """in_progress -> reported: work not completed; release for replanning.

        The committed block is released, never rewritten into a new
        proposal - see plan_execution_not_completed. The job becomes
        eligible for the next optimization attempt to produce a
        genuinely new proposal.
        """

        reporter = self._resolve_actor(actor)
        self.authorization.authorize(reporter, JobAction.REPORT_EXECUTION_NOT_COMPLETED)

        evidence = self._evidence_items(failure_evidence)

        with self.lifecycle_lock:
            job = self.repository.get(job_id)

            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            open_execution = self._current_open_execution(job, job_id)

            updated = self._transition(
                job_id,
                reporter,
                "execution_not_completed",
                plan_execution_not_completed(
                    job_id,
                    actor=reporter,
                    at=event_timestamp(self.clock()),
                    execution_id=execution_id,
                    actual_end_at=actual_end_at,
                    reason=reason,
                    execution=open_execution,
                    corridor_id=self.corridor.corridor_id,
                    horizon_minutes=self.horizon_minutes,
                    failure_evidence=evidence,
                    horizon_start=OPTIMIZATION_HORIZON_START,
                ),
            )

            records = build_execution_records(
                updated, self.history.list_for_job(job_id)
            )
            execution = self._reconstructed_execution(records, job_id, execution_id)

        return updated, execution

    def get_execution(
        self,
        job_id: str,
        actor: Actor | None = None,
    ) -> list[ExecutionRecord]:
        """Every execution of this job, oldest first, derived from history.

        KeyError if the job does not exist. An existing job that has
        never had a field execution returns an empty list - never a 404
        - since there is nothing wrong with that job, only an absence of
        executions to report.

        Reads the job row and its history together under lifecycle_lock.
        build_execution_records cross-checks the two (the row's own
        execution_id metadata against the EXECUTION_* event history), so
        without the lock a concurrent start/complete/not-completed could
        be observed mid-transition - e.g. the row already updated but
        the new event not yet visible - and be misread as a genuinely
        inconsistent history. Holding the lock guarantees this read
        observes either the state strictly before or strictly after any
        single transition, never a mix.
        """

        reader = self._resolve_actor(actor)
        self.authorization.authorize(reader, JobAction.READ_JOB_EXECUTION)

        with self.lifecycle_lock:
            job = self.repository.get(job_id)

            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            return build_execution_records(job, self.history.list_for_job(job_id))

    def _transition(
        self,
        job_id: str,
        actor: Actor,
        attempted: str,
        plan: Plan,
    ) -> dict[str, Any]:
        """Run one single-job transition under the lifecycle lock.

        A refused transition (invalid from the current state, stale, or
        against committed/terminal work) is recorded as
        TRANSITION_REJECTED and then re-raised unchanged, so the attempt
        is visible in history while the job's state is untouched.

        CommittedStateIntegrityError is recorded the same way: the event's
        error_type and reason name the integrity violation, and its
        before/after state show the inconsistent row exactly as stored.
        """

        with self.lifecycle_lock:
            try:
                return self.repository.mutate_jobs([job_id], plan)[job_id]

            except (
                InvalidTransitionError,
                StaleProposalError,
                StaleExecutionError,
                CommittedJobError,
                CommittedStateIntegrityError,
                ExecutionIntegrityError,
                TerminalJobError,
            ) as exc:
                self.record_rejected_transition([job_id], actor, attempted, exc)
                raise

    def record_rejected_transition(
        self,
        job_ids: list[str],
        actor: Actor,
        attempted: str,
        error: BaseException,
    ) -> None:
        """Append TRANSITION_REJECTED for each existing job. Changes no state."""

        at = event_timestamp()
        events = []

        for job_id in job_ids:
            job = self.repository.get(job_id)

            if job is not None:
                events.append(
                    rejected_transition_event(
                        job,
                        actor=actor,
                        attempted=attempted,
                        error=error,
                        at=at,
                    )
                )

        if events:
            self.repository.append_events(events)

    @staticmethod
    def _resolve_actor(actor: Actor | None) -> Actor:
        return actor if actor is not None else unidentified_actor()

    # -----------------------------------------
    # Used by Archit for optimization
    # -----------------------------------------

    def active_block_candidates(
        self,
    ) -> list[BlockCandidate]:
        """
        Return only jobs that can participate
        in future optimization/recovery.
        """

        active_jobs = (
            self.repository.list_active()
        )

        return [
            BlockCandidate.from_dict(
                job["block_candidate"]
            )
            for job in active_jobs
        ]

    # -----------------------------------------
    # Used when optimizer assigns schedule
    # -----------------------------------------

    def set_schedule(
        self,
        job_id: str,
        start_minute: int,
        end_minute: int,
        actor: Actor | None = None,
    ) -> dict[str, Any]:
        """Assign a placement directly, bypassing the optimizer.

        No HTTP route calls this. Completed work is terminal
        (TerminalJobError) and committed work cannot be re-placed
        (CommittedJobError); both refusals are recorded in history.
        """

        assigner = self._resolve_actor(actor)
        self.authorization.authorize(assigner, JobAction.ASSIGN_SCHEDULE)

        if self.repository.get(job_id) is None:
            raise KeyError(
                f"Job '{job_id}' not found"
            )

        if end_minute <= start_minute:
            raise ValueError(
                "schedule end must be greater "
                "than schedule start"
            )

        return self._transition(
            job_id,
            assigner,
            "assign_schedule",
            plan_schedule_assignment(
                job_id,
                start_minute,
                end_minute,
                actor=assigner,
                at=event_timestamp(),
            ),
        )

    # -----------------------------------------
    # Optimization input classification
    # -----------------------------------------

    def possession_inputs(
        self,
        horizon_start: str = OPTIMIZATION_HORIZON_START,
        horizon_minutes: Optional[int] = None,
    ) -> PossessionInputs:
        """Resolve this corridor's possession windows through ONE boundary.

        This is the jobs pipeline's single possession-input boundary
        (Sprint 3 Step 10). It SELECTS a source; it does not implement
        one. Neither branch parses a timetable, resolves a section or
        computes a train-free gap here - the canonical branch hands
        opaque records to backend.app.data.train_provider and receives
        finished windows, and the generator branch calls the generator.
        Possession derivation exists in exactly one place
        (backend.app.data.timetable_adapter) and is not re-expressed
        here.

        horizon_start is threaded through to the canonical conversion,
        NOT re-derived: the caller passes the same value it puts on the
        OptimizationRequest, so the traversals, the windows and the
        request all share one anchor. See
        backend.app.jobs.optimization.JobOptimizationService.optimize_corridor,
        which resolves both horizon values once and passes them to this
        method and to the request builder alike.

        horizon_minutes defaults to this SERVICE INSTANCE's own
        self.horizon_minutes (Slice 4 Step 2) rather than to the module
        constant OPTIMIZATION_HORIZON_MINUTES directly - a default
        parameter value is bound at class-definition time and cannot
        read self, so None is the sentinel meaning "use this instance's
        deployment horizon." An explicit caller-supplied value (several
        tests do this deliberately) still overrides it exactly as
        before.
        """

        if horizon_minutes is None:
            horizon_minutes = self.horizon_minutes

        if self.dataset is not None and self.dataset.timetable_records:
            return self._canonical_possession_inputs(
                horizon_start,
                horizon_minutes,
            )

        return self._generated_possession_inputs(horizon_minutes)

    def _canonical_possession_inputs(
        self,
        horizon_start: str,
        horizon_minutes: int,
    ) -> PossessionInputs:
        """Train-free gaps derived from canonical section traversals.

        The provider is constructed with this corridor's own timetable
        records and topology, and labelled SYNTHETIC_SCHEDULED because
        every timetable checked into this repository is synthetic demo
        data - passing records through a canonical adapter does not make
        them real, and the provider itself has no LIVE value to give
        them.

        A malformed train record is NOT fatal here: convert_timetable
        records it as a TrainRejection and the rest of the batch
        continues, while possession derivation withholds every section
        that train might have occupied. A rejection can therefore only
        cost maintenance opportunity, never create it.

        TIMETABLE COVERAGE GATE (Slice 4 Step 4). Before any possession
        window is derived, every calendar date the horizon touches must
        be asserted by at least one timetable record's service_date -
        see backend.app.data.timetable_coverage, reused here unchanged.
        This is checked here, ahead of possession_windows_from_provider,
        because THAT function's own derivation reads missing occupation
        as a train-free gap - correct for a day the timetable actually
        describes, wrong for a day it never mentioned at all. A single
        uncovered date raises TimetableCoverageGapError and refuses this
        call outright: no window is derived for ANY date in the request,
        matching PossessionDataUnavailableError's own existing
        all-or-nothing refusal shape rather than silently offering
        possession for the covered days alone.
        """

        dataset = self.dataset

        coverage = TimetableCoverage.from_timetable_records(
            dataset.timetable_records
        )
        uncovered_dates = uncovered_horizon_days(
            coverage, horizon_start, horizon_minutes
        )

        if uncovered_dates:
            raise TimetableCoverageGapError(
                corridor_id=self.corridor.corridor_id,
                horizon_start=str(horizon_start),
                horizon_minutes=horizon_minutes,
                required_dates=horizon_calendar_dates(
                    horizon_start, horizon_minutes
                ),
                covered_dates=sorted(coverage.covered_service_dates),
                uncovered_dates=uncovered_dates,
            )

        provider = StaticTimetableProvider(
            dataset.timetable_records,
            dataset.topology,
            provenance=TrainDataProvenance.SYNTHETIC_SCHEDULED.value,
            horizon_start=horizon_start,
        )

        windows, snapshot = possession_windows_from_provider(
            provider,
            dataset.topology,
            horizon_minutes=horizon_minutes,
            registry=dataset.registry,
        )

        timetable_provenance = from_train_data_provenance(
            snapshot.provenance
        )

        return PossessionInputs(
            windows=windows,
            derivation=POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
            timetable_provenance=timetable_provenance,
            possession_provenance=derived_possession_provenance(
                timetable_provenance
            ),
            snapshot=snapshot,
        )

    def _generated_possession_inputs(
        self,
        horizon_minutes: int,
    ) -> PossessionInputs:
        """The pre-Step-10 generator slots, for a corridor with no timetable.

        These are GENERATED_STATIC operational slots (night block,
        midday slot, evening off-peak) that model no train movement at
        all - see POSSESSION_SOURCE_GENERATED_STATIC and
        DEFAULT_POSSESSION_COMPATIBILITY. The timetable axis is reported
        SYNTHETIC because no timetable is consulted on this path, not
        because a synthetic one was used.

        registry=self.registry is passed through (Sprint 3 Step 11) so
        these windows carry the SAME section_id
        generate_candidate_blocks/resolve_job_resource resolve jobs
        against - see generate_possession_windows, which returns
        section_id=None when given no registry. Before Step 11 that
        was harmless because every job block ALSO carried
        section_id=None, so solver._possession_window_covers_block's
        track-only fallback covered both sides identically. Once jobs
        resolve a real section_id, a window still stuck at None would
        stop matching them at all (None != "CORRIDOR_A_S0-...") and
        every job would silently go unscheduled - passing the registry
        here is what keeps possession coverage aligned with the
        resolved resource on both sides of the match.
        """

        generator = CorridorDataGenerator(seed=42)

        windows = generator.generate_possession_windows(
            self.corridor.tracks,
            horizon_minutes=horizon_minutes,
            registry=self.registry,
        )

        return PossessionInputs(
            windows=windows,
            derivation=POSSESSION_DERIVATION_GENERATED_SLOTS,
            timetable_provenance=ProvenanceLevel.SYNTHETIC,
            possession_provenance=ProvenanceLevel.SYNTHETIC,
            snapshot=None,
        )

    def possession_windows(
        self,
        horizon_start: str = OPTIMIZATION_HORIZON_START,
        horizon_minutes: Optional[int] = None,
    ) -> list[PossessionWindow]:
        """Just the windows, for callers that need nothing else.

        Kept as the pre-Step-10 entry point so existing callers keep
        working unchanged. Production code should prefer
        possession_inputs, which also carries the provenance axes and
        the train rejections that explain a missing window.

        horizon_minutes: see possession_inputs - None (the default)
        defers to this instance's own self.horizon_minutes.
        """

        return self.possession_inputs(
            horizon_start,
            horizon_minutes,
        ).windows

    def classify_for_optimization(
        self,
    ) -> tuple[list[BlockCandidate], list[BlockCandidate]]:
        """Split active jobs into (all candidates, committed subset).

        Per the approved lifecycle:
          reported                -> free candidate
          scheduled, not notified -> free candidate, may be replanned
          notified                -> committed, must be preserved
          completed               -> terminal, excluded entirely

        The committed subset is a subset of the candidate list, not a
        separate collection: the solver iterates request.candidates and
        looks up committed placements by block_id, so a committed block
        that is absent from candidates would never be modelled at all.
        """

        candidates, committed, _ = self.optimization_snapshot()

        return candidates, committed

    def optimization_snapshot(
        self,
    ) -> tuple[list[BlockCandidate], list[BlockCandidate], dict[str, str]]:
        """classify_for_optimization plus each job's status at read time.

        The statuses are what JobOptimizationService checks again inside
        the write transaction, so an outcome is never applied to a job
        that changed after the solver saw it. All three come from ONE
        read, so they cannot disagree with each other.

        Fails closed with CommittedStateIntegrityError if any active job's
        stored committed state is inconsistent: the solver would otherwise
        pin (or fail to pin) a commitment whose integrity is unknown.
        """

        candidates: list[BlockCandidate] = []
        committed: list[BlockCandidate] = []
        statuses: dict[str, str] = {}

        active = self.repository.list_active()
        assert_committed_state_consistent(active)

        for job in active:
            block = BlockCandidate.from_dict(
                job["block_candidate"]
            )

            candidates.append(block)
            statuses[job["job_id"]] = job["status"]

            if job["status"] in COMMITTED_STATUSES:
                committed.append(block)

        return candidates, committed, statuses

    # -----------------------------------------
    # Asset selection
    # -----------------------------------------

    def _nearest_asset(
        self,
        track_id: str,
        distance_start: float,
        distance_end: float,
    ):

        midpoint_km = (
            (distance_start + distance_end)
            / 2.0
            / 1000.0
        )

        candidates = [
            asset
            for asset in self.corridor.assets
            if asset.track_id == track_id
        ]

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda asset:
                abs(
                    asset.km_location
                    - midpoint_km
                ),
        )


def _no_proposal_reason(job: Mapping[str, Any]) -> str:
    """Human-readable reason a job in a non-'scheduled' status has no proposal."""

    status = job["status"]

    if status == JobStatus.REPORTED.value:
        if job.get("last_refusal_reason"):
            return (
                "job was considered and left UNSCHEDULED: "
                f"{job['last_refusal_reason']}"
            )
        return "job has not been through optimization yet"

    if status == JobStatus.NOTIFIED.value:
        return "job's proposal was already committed via notify; it is no longer a pending proposal"

    if status == JobStatus.IN_PROGRESS.value:
        return "job's committed block is being executed; it is no longer a pending proposal"

    if status == JobStatus.COMPLETED.value:
        return "job is completed"

    return f"job is in status {status!r}"


def as_public_job(
    job: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": job["job_id"],
        "track_id": job["track_id"],
        "work_type": job["work_type"],
        "distance_start":
            job["distance_start"],
        "distance_end":
            job["distance_end"],
        "workers_min":
            job["workers_min"],
        "workers_max":
            job["workers_max"],
        "description":
            job["description"],
        "status":
            job["status"],
        "priority_score":
            job["priority_score"],
        "risk_score":
            job["risk_score"],
        "schedule_start_minute":
            job["schedule_start_minute"],
        "schedule_end_minute":
            job["schedule_end_minute"],
        "created_at":
            job["created_at"],
        "updated_at":
            job.get("updated_at"),
        "last_solver_status":
            job.get("last_solver_status"),
        "last_refusal_reason":
            job.get("last_refusal_reason"),
        "proposal_run_id":
            proposal_run_id_of(job),
        "block_candidate":
            job["block_candidate"],
    }