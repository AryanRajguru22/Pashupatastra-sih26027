from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from contracts import BlockCandidate, BlockStatus

from backend.app.data.feature_adapter import (
    ScoringFeatureAdapter,
    WORK_TYPE_DEFAULT_DURATIONS,
)

from backend.app.data.generator import (
    CorridorDataGenerator,
)

from backend.app.data.models import Corridor

from backend.app.jobs.models import (
    JobCreateRequest,
    JobStatus,
)

from backend.app.jobs.repository import (
    JobRepository,
)


DEFAULT_CORRIDOR_ID = os.getenv(
    "PASHUPAT_CORRIDOR_ID",
    "CORRIDOR_A",
)


class JobService:

    def __init__(
        self,
        repository: JobRepository | None = None,
        corridor: Corridor | None = None,
    ):
        self.repository = (
            repository or JobRepository()
        )

        self.corridor = (
            corridor
            or self._build_default_corridor()
        )

    @staticmethod
    def _build_default_corridor() -> Corridor:

        generator = CorridorDataGenerator(
            seed=42
        )

        corridor_id = DEFAULT_CORRIDOR_ID

        if corridor_id == "CORRIDOR_B_DENSE":

            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=4,
                corridor_length_km=60.0,
            )

        elif corridor_id == "CORRIDOR_C_DISRUPTED":

            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=2,
                corridor_length_km=40.0,
            )

        else:

            tracks = generator.generate_corridor_tracks(
                corridor_id,
                num_tracks=2,
                corridor_length_km=35.0,
            )

        assets = generator.generate_assets(
            tracks,
            num_assets_per_track=8,
        )

        return Corridor(
            corridor_id=corridor_id,
            name=corridor_id.replace(
                "_",
                " ",
            ).title(),
            tracks=tracks,
            assets=assets,
        )

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
        # 4. Create canonical BlockCandidate
        # ---------------------------------

        block = BlockCandidate(
            block_id=(
                f"JOB-"
                f"{uuid.uuid4().hex[:10].upper()}"
            ),

            asset_id=asset.asset_id,

            track_id=request.track_id,

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

        created_at = (
            datetime.now(timezone.utc)
            .isoformat()
        )

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

        return self.repository.update_status(
            job_id,
            JobStatus.NOTIFIED.value,
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

        return self.repository.update_schedule(
            job_id,
            start_minute,
            end_minute,
        )

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
        "block_candidate":
            job["block_candidate"],
    }