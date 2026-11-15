"""Application service for the persisted DISRUPT -> RECOVER workflow.

See backend.app.jobs.disruption for the model. This class only
orchestrates: authorize, validate, record, and drive a recovery run
through the existing JobOptimizationService. It shares that service's
JobService, lifecycle lock and authorization seam, so every call is
covered by the same policy (including enforced NovaForge mode).
"""

from __future__ import annotations

from typing import Any, Optional

from backend.app.identity.actor import Actor, unidentified_actor
from backend.app.identity.authorization import JobAction
from backend.app.jobs.disruption import (
    DisruptionEventType,
    DisruptionKind,
    DisruptionNotFoundError,
    DisruptionRecord,
    DisruptionRepository,
    DisruptionStateError,
    RecoveryBlockedError,
    affected_jobs,
    compare_plans,
    new_disruption_id,
    validate_declaration,
)
from backend.app.jobs.events import event_timestamp
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.service import OPTIMIZATION_HORIZON_START

_PLACED = ("scheduled", "notified", "in_progress")


class DisruptionService:
    def __init__(self, optimization_service: JobOptimizationService):
        self.optimization = optimization_service
        self.service = optimization_service.service
        self.repository: DisruptionRepository = optimization_service.disruptions
        self._lock = self.service.lifecycle_lock

    # ------------------------------------------------------------------
    def _authorize(self, actor: Optional[Actor], action: JobAction, corridor_id: str) -> Actor:
        who = actor if actor is not None else unidentified_actor()
        self.service.authorization.authorize(who, action)
        self._check_corridor(corridor_id)
        self.service.authorize_corridor(who, action, corridor_id)
        return who

    def _check_corridor(self, corridor_id: str) -> None:
        if corridor_id != self.service.corridor.corridor_id:
            raise KeyError(
                f"Unknown corridor '{corridor_id}'. This deployment serves "
                f"corridor '{self.service.corridor.corridor_id}'."
            )

    def _placements(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for job in self.service.repository.list_active():
            if (
                job["status"] in _PLACED
                and job.get("schedule_start_minute") is not None
                and job.get("schedule_end_minute") is not None
            ):
                out[job["job_id"]] = {
                    "status": job["status"],
                    "start_minute": int(job["schedule_start_minute"]),
                    "end_minute": int(job["schedule_end_minute"]),
                    "track_id": job["track_id"],
                    "section_id": (job.get("block_candidate") or {}).get("section_id"),
                }
        return out

    def _view(self, record: DisruptionRecord) -> dict[str, Any]:
        impacts = (
            affected_jobs(
                self.service.repository.list_active(),
                [record],
                OPTIMIZATION_HORIZON_START,
            )
            if record.status == "ACTIVE"
            else []
        )
        view = record.to_dict()
        view["affected_jobs"] = impacts
        view["blocking_recovery"] = [
            entry["job_id"]
            for entry in impacts
            if entry["impact"] != "PROPOSAL_REPLAN"
        ]
        return view

    # ------------------------------------------------------------------
    def register(
        self,
        corridor_id: str,
        actor: Optional[Actor],
        *,
        kind: DisruptionKind,
        track_id: str,
        section_id: Optional[str],
        starts_at: str,
        ends_at: str,
        reason: str,
    ) -> dict[str, Any]:
        who = self._authorize(actor, JobAction.REGISTER_DISRUPTION, corridor_id)

        validate_declaration(
            kind=kind,
            track_id=track_id,
            section_id=section_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            known_tracks=[t.track_id for t in self.service.corridor.tracks],
            known_sections=self.service.registry.section_ids(),
            horizon_start=OPTIMIZATION_HORIZON_START,
            horizon_minutes=self.service.horizon_minutes,
        )

        disruption_id = new_disruption_id()

        with self._lock:
            self.repository.append(
                disruption_id,
                DisruptionEventType.REGISTERED,
                actor=who,
                at=event_timestamp(self.service.clock()),
                payload={
                    "kind": kind.value,
                    "corridor_id": corridor_id,
                    "track_id": track_id,
                    "section_id": section_id,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "reason": reason.strip(),
                    "basis": "SIMULATED_DECLARED",
                },
            )
            return self._view(self.repository.get(disruption_id))

    def clear(
        self,
        corridor_id: str,
        disruption_id: str,
        actor: Optional[Actor],
        *,
        reason: str,
    ) -> dict[str, Any]:
        who = self._authorize(actor, JobAction.CLEAR_DISRUPTION, corridor_id)

        if not reason or not reason.strip():
            raise ValueError("A reason is required to clear a disruption.")

        with self._lock:
            record = self.repository.get(disruption_id)
            if record.corridor_id != corridor_id:
                raise DisruptionNotFoundError(f"Unknown disruption '{disruption_id}'.")
            if record.status != "ACTIVE":
                raise DisruptionStateError(
                    f"Disruption '{disruption_id}' is already cleared."
                )
            self.repository.append(
                disruption_id,
                DisruptionEventType.CLEARED,
                actor=who,
                at=event_timestamp(self.service.clock()),
                payload={"reason": reason.strip()},
            )
            return self._view(self.repository.get(disruption_id))

    def list_disruptions(self, corridor_id: str, actor: Optional[Actor]) -> list[dict[str, Any]]:
        self._authorize(actor, JobAction.READ_DISRUPTION, corridor_id)
        return [
            self._view(record)
            for record in self.repository.all()
            if record.corridor_id == corridor_id
        ]

    def get(self, corridor_id: str, disruption_id: str, actor: Optional[Actor]) -> dict[str, Any]:
        self._authorize(actor, JobAction.READ_DISRUPTION, corridor_id)
        record = self.repository.get(disruption_id)
        if record.corridor_id != corridor_id:
            raise DisruptionNotFoundError(f"Unknown disruption '{disruption_id}'.")
        view = self._view(record)
        view["events"] = self.repository.events(disruption_id)
        return view

    # ------------------------------------------------------------------
    def recover(
        self,
        corridor_id: str,
        disruption_id: str,
        actor: Optional[Actor],
    ) -> dict[str, Any]:
        """Re-plan around the active disruptions and record what changed.

        A real optimization run (same solver, same constraints) over the
        possession windows MINUS every active disruption, with approved
        work still pinned. It is refused outright - before the solver
        runs - while approved work overlaps a disruption; see
        RecoveryBlockedError. Authorized as REQUEST_OPTIMIZATION.
        """

        who = self._authorize(actor, JobAction.REQUEST_OPTIMIZATION, corridor_id)

        with self._lock:
            record = self.repository.get(disruption_id)
            if record.corridor_id != corridor_id:
                raise DisruptionNotFoundError(f"Unknown disruption '{disruption_id}'.")
            if record.status != "ACTIVE":
                raise DisruptionStateError(
                    f"Disruption '{disruption_id}' is cleared; there is "
                    "nothing to recover from. Use a normal optimization."
                )

            before = self._placements()

            try:
                outcome = self.optimization.optimize_corridor(
                    corridor_id, actor=who, trigger="recovery"
                )
            except RecoveryBlockedError as exc:
                self.repository.append(
                    disruption_id,
                    DisruptionEventType.RECOVERY_REFUSED,
                    actor=who,
                    at=event_timestamp(self.service.clock()),
                    payload={
                        "reason": str(exc),
                        "blocking": exc.blocking,
                    },
                )
                raise

            after = self._placements()
            comparison = compare_plans(before, after)

            reasons = {
                entry["job_id"]: entry.get("reason")
                for entry in outcome.get("unscheduled", [])
            }
            for item in comparison["newly_unscheduled"]:
                item["reason"] = reasons.get(item["job_id"])

            active_ids = [d.disruption_id for d in self.repository.active()]

            self.repository.append(
                disruption_id,
                DisruptionEventType.RECOVERY_PLANNED,
                actor=who,
                at=event_timestamp(self.service.clock()),
                payload={
                    "optimization_run_id": outcome["optimization_run_id"],
                    "solver_status": outcome["solver_status"],
                    "active_disruption_ids": active_ids,
                    "counts": outcome["counts"],
                    "comparison_counts": comparison["counts"],
                },
            )

            return {
                "disruption": self._view(self.repository.get(disruption_id)),
                "optimization": outcome,
                "comparison": comparison,
                "active_disruption_ids": active_ids,
            }


__all__ = ["DisruptionService"]
