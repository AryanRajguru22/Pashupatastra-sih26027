from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from contracts import DEFAULT_HORIZON_START, BlockCandidate, BlockStatus

from backend.app.data.canonical_train import (
    TrainDataProvenance,
    TrainDataSnapshot,
)

from backend.app.data.corridor_dataset import (
    CorridorDataset,
    load_corridor_dataset,
)

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

from backend.app.data.train_provider import (
    StaticTimetableProvider,
    possession_windows_from_provider,
)

from contracts import PossessionWindow

from backend.app.data.models import Corridor

from backend.app.jobs.models import (
    JobCreateRequest,
    JobStatus,
)

from backend.app.jobs.repository import (
    TERMINAL_STATUSES,
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
COMMITTED_STATUSES = ("notified",)

# The corridor generator, the shipped fixtures and BlockCandidate's own
# default latest_end_minute all use a 1440-minute (24h) planning day.
# horizon_start/OPTIMIZATION_HORIZON_MINUTES are coupled: both define
# the same single-day jobs-pipeline horizon and must move together if
# this pipeline ever adopts a longer planning window.
OPTIMIZATION_HORIZON_START = DEFAULT_HORIZON_START
OPTIMIZATION_HORIZON_MINUTES = 1440
DEFAULT_MIN_HEADWAY_MINUTES = 15


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def __init__(
        self,
        repository: JobRepository | None = None,
        corridor: Corridor | None = None,
        dataset: CorridorDataset | None = None,
        registry: SectionRegistry | None = None,
    ):
        self.repository = (
            repository or JobRepository()
        )

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
    ) -> dict[str, Any]:

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

            latest_end_minute=1440,

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
            },
        )

        # ---------------------------------
        # 5. Use EXISTING scorer
        # ---------------------------------

        scored_block = (
            ScoringFeatureAdapter.score_block_candidate(
                block,
                self.corridor,
            )
        )

        # ---------------------------------
        # 6. Store in DB
        # ---------------------------------

        created_at = _now()

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

        return self.repository.create(job)

    # -----------------------------------------
    # GET /jobs support
    # -----------------------------------------

    def list_jobs(self) -> list[dict[str, Any]]:

        return self.repository.list_all()

    # -----------------------------------------
    # scheduled -> notified
    # -----------------------------------------

    def notify(
        self,
        job_id: str,
    ) -> dict[str, Any]:

        job = self.repository.get(job_id)

        if job is None:
            raise KeyError(
                f"Job '{job_id}' not found"
            )

        if job["status"] != (
            JobStatus.SCHEDULED.value
        ):
            raise ValueError(
                f"Job '{job_id}' cannot be "
                "notified from status "
                f"'{job['status']}'"
            )

        # Notifying is what makes the work operationally committed:
        # field personnel have been told to be on the track. The
        # persisted BlockCandidate is marked committed here so that a
        # later optimization reconstructs it as an existing committed
        # block and the solver preserves its placement.
        return self.repository.update_status(
            job_id,
            JobStatus.NOTIFIED.value,
            updated_at=_now(),
            block_status=BlockStatus.COMMITTED.value,
            is_committed=True,
        )

    # -----------------------------------------
    # notified -> completed
    # -----------------------------------------

    def complete(
        self,
        job_id: str,
    ) -> dict[str, Any]:

        job = self.repository.get(job_id)

        if job is None:
            raise KeyError(
                f"Job '{job_id}' not found"
            )

        if job["status"] != (
            JobStatus.NOTIFIED.value
        ):
            raise ValueError(
                f"Job '{job_id}' cannot be "
                "completed from status "
                f"'{job['status']}'"
            )

        return self.repository.update_status(
            job_id,
            JobStatus.COMPLETED.value,
            updated_at=_now(),
        )

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
    ) -> dict[str, Any]:

        job = self.repository.get(job_id)

        if job is None:
            raise KeyError(
                f"Job '{job_id}' not found"
            )

        if end_minute <= start_minute:
            raise ValueError(
                "schedule end must be greater "
                "than schedule start"
            )

        # Completed work is terminal. Without this guard a completed
        # job was silently forced back to 'scheduled' and re-entered
        # the optimization candidate set. The repository enforces the
        # same rule, so the guarantee holds even for callers that
        # bypass this service.
        if job["status"] in TERMINAL_STATUSES:
            raise TerminalJobError(
                f"Job '{job_id}' is in terminal status "
                f"'{job['status']}' and cannot be rescheduled"
            )

        return self.repository.update_schedule(
            job_id,
            start_minute,
            end_minute,
            updated_at=_now(),
        )

    # -----------------------------------------
    # Optimization input classification
    # -----------------------------------------

    def possession_inputs(
        self,
        horizon_start: str = OPTIMIZATION_HORIZON_START,
        horizon_minutes: int = OPTIMIZATION_HORIZON_MINUTES,
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
        """

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
        """

        dataset = self.dataset

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
        horizon_minutes: int = OPTIMIZATION_HORIZON_MINUTES,
    ) -> list[PossessionWindow]:
        """Just the windows, for callers that need nothing else.

        Kept as the pre-Step-10 entry point so existing callers keep
        working unchanged. Production code should prefer
        possession_inputs, which also carries the provenance axes and
        the train rejections that explain a missing window.
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

        candidates: list[BlockCandidate] = []
        committed: list[BlockCandidate] = []

        for job in self.repository.list_active():
            block = BlockCandidate.from_dict(
                job["block_candidate"]
            )

            candidates.append(block)

            if job["status"] in COMMITTED_STATUSES:
                committed.append(block)

        return candidates, committed

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
        "block_candidate":
            job["block_candidate"],
    }