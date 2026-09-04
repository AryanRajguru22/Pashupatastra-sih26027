"""CP-SAT block scheduling engine.

Consumes the shared OptimizationRequest contract and produces
OptimizationResult using OR-Tools CP-SAT.

Hard constraints:
- block time windows
- track no-overlap
- configured minimum headway
- dependency ordering
- mutual-exclusion groups
- possession windows
- committed-block pinning

priority_score and risk_score are objective weights only.
"""

from __future__ import annotations

from typing import Dict, List, Set

from ortools.sat.python import cp_model

from contracts import (
    BlockCandidate,
    BlockStatus,
    OptimizationRequest,
    OptimizationResult,
    ScheduledBlock,
    SolverStatus,
)


_SCORE_SCALE = 1000
_SOLVE_TIME_LIMIT_SECONDS = 10.0


def _objective_score(
    request: OptimizationRequest,
    block: BlockCandidate,
) -> float:
    """Calculate the normalized objective contribution of one block.

    The new shared contract does not expose separate objective-weight
    configuration, so risk and priority are combined directly.
    """
    return block.risk_score + block.priority_score


def _clamp_window(
    block: BlockCandidate,
    horizon_minutes: int,
) -> tuple[int, int, int]:
    """Return feasible [earliest, latest_end, latest_start] values.

    The optimizer operates entirely in integer minutes relative to the
    planning horizon.
    """
    earliest = max(0, int(block.earliest_start_minute))
    latest_end = min(horizon_minutes, int(block.latest_end_minute))
    latest_start = latest_end - int(block.duration_minutes)

    return earliest, latest_end, latest_start


def solve(request: OptimizationRequest) -> OptimizationResult:
    """Solve an optimization request using CP-SAT."""

    horizon_minutes = max(0, int(request.horizon_minutes))
    blocks: List[BlockCandidate] = list(request.candidates)

    by_id: Dict[str, BlockCandidate] = {
        block.block_id: block for block in blocks
    }

    model = cp_model.CpModel()

    presence: Dict[str, cp_model.IntVar] = {}
    start: Dict[str, cp_model.IntVar] = {}
    end: Dict[str, cp_model.IntVar] = {}

    # Blocks which cannot fit inside their own time windows.
    window_infeasible: Set[str] = set()

    # ------------------------------------------------------------------
    # Committed blocks
    # ------------------------------------------------------------------
    # A committed block from a previous plan is pinned to exactly the
    # same start/end placement during re-optimization.
    committed_by_id: Dict[str, BlockCandidate] = {
        block.block_id: block
        for block in request.existing_committed_blocks
    }

    # ------------------------------------------------------------------
    # Create variables
    # ------------------------------------------------------------------
    for block in blocks:
        earliest, latest_end, latest_start = _clamp_window(
            block,
            horizon_minutes,
        )

        presence_var = model.NewBoolVar(
            f"presence_{block.block_id}"
        )
        presence[block.block_id] = presence_var

        # A block whose duration cannot fit in its own time window must
        # simply remain unscheduled. This keeps CP-SAT domains valid.
        if latest_start < earliest:
            window_infeasible.add(block.block_id)

            model.Add(presence_var == 0)

            start_var = model.NewIntVar(
                earliest,
                earliest,
                f"start_{block.block_id}",
            )

            end_var = model.NewIntVar(
                earliest,
                earliest,
                f"end_{block.block_id}",
            )

        else:
            start_var = model.NewIntVar(
                earliest,
                latest_start,
                f"start_{block.block_id}",
            )

            end_var = model.NewIntVar(
                earliest,
                latest_end,
                f"end_{block.block_id}",
            )

            model.Add(
                end_var
                == start_var + int(block.duration_minutes)
            )

        # --------------------------------------------------------------
        # Committed block pinning
        # --------------------------------------------------------------
        committed = committed_by_id.get(block.block_id)

        if committed is not None:
            committed_start = int(committed.earliest_start_minute)

            # Prefer metadata values for an exact old placement when
            # available. The shared contract represents block windows,
            # so for committed candidates we use the candidate's exact
            # start/end values when encoded in metadata.
            committed_end = None

            if "committed_start_minute" in committed.metadata:
                committed_start = int(
                    committed.metadata["committed_start_minute"]
                )

            if "committed_end_minute" in committed.metadata:
                committed_end = int(
                    committed.metadata["committed_end_minute"]
                )

            if committed_end is None:
                committed_end = committed_start + int(
                    committed.duration_minutes
                )

            model.Add(presence_var == 1)
            model.Add(start_var == committed_start)
            model.Add(end_var == committed_end)

        start[block.block_id] = start_var
        end[block.block_id] = end_var

    # ------------------------------------------------------------------
    # Possession windows
    # ------------------------------------------------------------------
    # A block may only be scheduled if it lies completely inside at
    # least one possession window belonging to the same track.
    for block in blocks:
        if block.block_id in window_infeasible:
            continue

        matching_windows = [
            window
            for window in request.possession_windows
            if window.track_id == block.track_id
        ]

        # No possession windows means that there is no additional
        # possession restriction for this request.
        if not matching_windows:
            continue

        eligible_windows = []

        for index, window in enumerate(matching_windows):
            window_start = max(
                0,
                int(window.start_minute),
            )
            window_end = min(
                horizon_minutes,
                int(window.end_minute),
            )

            # Skip impossible/empty windows.
            if window_end <= window_start:
                continue

            selection = model.NewBoolVar(
                f"possession_{block.block_id}_{index}"
            )

            eligible_windows.append(selection)

            model.Add(
                start[block.block_id] >= window_start
            ).OnlyEnforceIf(selection)

            model.Add(
                end[block.block_id] <= window_end
            ).OnlyEnforceIf(selection)

            model.Add(
                selection <= presence[block.block_id]
            )

        if not eligible_windows:
            model.Add(presence[block.block_id] == 0)

        else:
            # If the block is scheduled, exactly one possession window
            # must contain it. If it is unscheduled, none is selected.
            model.Add(
                sum(eligible_windows)
                == presence[block.block_id]
            )

    # ------------------------------------------------------------------
    # Track no-overlap + headway
    # ------------------------------------------------------------------
    headway = max(0, int(request.min_headway_minutes))

    by_track: Dict[str, List[str]] = {}

    for block in blocks:
        by_track.setdefault(block.track_id, []).append(
            block.block_id
        )

    for track_id, block_ids in by_track.items():
        buffered_intervals = []

        for block_id in block_ids:
            block = by_id[block_id]

            buffered_size = (
                int(block.duration_minutes) + headway
            )

            buffered_end = model.NewIntVar(
                0,
                horizon_minutes + buffered_size,
                f"buffered_end_{block_id}",
            )

            model.Add(
                buffered_end
                == start[block_id] + buffered_size
            )

            buffered_intervals.append(
                model.NewOptionalIntervalVar(
                    start[block_id],
                    buffered_size,
                    buffered_end,
                    presence[block_id],
                    f"buffered_interval_{block_id}",
                )
            )

        model.AddNoOverlap(buffered_intervals)

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------
    for block in blocks:
        for dep_id in block.dependencies:
            # Preserve the existing v0.1 behavior for an unknown
            # dependency: it cannot be modeled without a candidate.
            if dep_id not in by_id:
                continue

            # Dependent block cannot exist without its dependency.
            model.Add(
                presence[block.block_id]
                <= presence[dep_id]
            )

            # If the dependent block is scheduled, the dependency
            # must finish before it starts.
            model.Add(
                start[block.block_id] >= end[dep_id]
            ).OnlyEnforceIf(
                presence[block.block_id]
            )

    # ------------------------------------------------------------------
    # Mutual exclusion groups
    # ------------------------------------------------------------------
    # All blocks in the same mutual_exclusion_group share a resource,
    # such as a crew/resource pool, and therefore cannot overlap.
    by_mutex_group: Dict[str, List[str]] = {}

    for block in blocks:
        group = block.mutual_exclusion_group

        if group:
            by_mutex_group.setdefault(group, []).append(
                block.block_id
            )

    for group, block_ids in by_mutex_group.items():
        intervals = []

        for block_id in block_ids:
            block = by_id[block_id]

            intervals.append(
                model.NewOptionalIntervalVar(
                    start[block_id],
                    int(block.duration_minutes),
                    end[block_id],
                    presence[block_id],
                    f"mutex_{group}_{block_id}",
                )
            )

        model.AddNoOverlap(intervals)

    # ------------------------------------------------------------------
    # Objective
    # ------------------------------------------------------------------
    # The new shared contract exposes risk_score and priority_score
    # directly on BlockCandidate. Since no objective-weight structure is
    # present in the new contract, both contribute directly and equally.
    objective_terms = []

    for block in blocks:
        coefficient = round(
            _SCORE_SCALE
            * _objective_score(request, block)
        )

        objective_terms.append(
            coefficient * presence[block.block_id]
        )

    if objective_terms:
        model.Maximize(sum(objective_terms))

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = (
        _SOLVE_TIME_LIMIT_SECONDS
    )

    solve_status = solver.Solve(model)

    status_map = {
        cp_model.OPTIMAL: SolverStatus.OPTIMAL.value,
        cp_model.FEASIBLE: SolverStatus.FEASIBLE.value,
        cp_model.INFEASIBLE: SolverStatus.INFEASIBLE.value,
        cp_model.UNKNOWN: SolverStatus.NO_SOLUTION.value,
    }

    status = status_map.get(
        solve_status,
        SolverStatus.NO_SOLUTION.value,
    )

    solve_time_seconds = float(solver.WallTime())

    # ------------------------------------------------------------------
    # Explicit infeasibility / no-solution result
    # ------------------------------------------------------------------
    if status not in (
        SolverStatus.OPTIMAL.value,
        SolverStatus.FEASIBLE.value,
    ):
        reasons = [
            "solver found no feasible solution"
        ]

        unscheduled = list(blocks)

        rejection_reasons = {
            block.block_id:
            "solver found no feasible solution"
            for block in blocks
        }

        return OptimizationResult(
            corridor_id=request.corridor_id,
            status=status,
            scheduled_blocks=[],
            unscheduled_blocks=unscheduled,
            total_priority_scheduled=0.0,
            total_risk_mitigated=0.0,
            solve_time_seconds=solve_time_seconds,
            infeasibility_reasons=reasons,
            rejection_reasons=rejection_reasons,
        )

    # ------------------------------------------------------------------
    # Build result
    # ------------------------------------------------------------------
    scheduled_blocks: List[ScheduledBlock] = []
    unscheduled_blocks: List[BlockCandidate] = []
    scheduled_ids: Set[str] = set()

    for block in blocks:
        block_id = block.block_id

        if solver.Value(presence[block_id]):
            start_minute = int(
                solver.Value(start[block_id])
            )
            end_minute = int(
                solver.Value(end[block_id])
            )

            is_committed = (
                block.is_committed
                or block_id in committed_by_id
            )

            scheduled_blocks.append(
                ScheduledBlock(
                    block_id=block_id,
                    track_id=block.track_id,
                    start_minute=start_minute,
                    end_minute=end_minute,
                    work_type=block.work_type,
                    priority_score=block.priority_score,
                    risk_score=block.risk_score,
                    is_committed=is_committed,
                    status=(
                        BlockStatus.COMMITTED.value
                        if is_committed
                        else BlockStatus.SCHEDULED.value
                    ),
                )
            )

            scheduled_ids.add(block_id)

    # ------------------------------------------------------------------
    # Explain unscheduled blocks through rejection_reasons
    # ------------------------------------------------------------------
    rejection_reasons: Dict[str, str] = {}

    for block in blocks:
        block_id = block.block_id

        if block_id in scheduled_ids:
            continue

        if block_id in window_infeasible:
            reason = (
                "duration does not fit within "
                "earliest_start_minute/latest_end_minute window"
            )

        else:
            unscheduled_dependency = next(
                (
                    dep_id
                    for dep_id in block.dependencies
                    if (
                        dep_id in by_id
                        and dep_id not in scheduled_ids
                    )
                ),
                None,
            )

            if unscheduled_dependency is not None:
                reason = (
                    "dependency not scheduled: "
                    f"{unscheduled_dependency}"
                )

            else:
                scheduled_mutex = None

                if block.mutual_exclusion_group:
                    scheduled_mutex = next(
                        (
                            other_id
                            for other_id in scheduled_ids
                            if (
                                by_id[other_id]
                                .mutual_exclusion_group
                                == block.mutual_exclusion_group
                            )
                        ),
                        None,
                    )

                if scheduled_mutex is not None:
                    reason = (
                        "mutual exclusion group conflict with "
                        f"scheduled block: {scheduled_mutex}"
                    )

                else:
                    # Look for a same-track scheduled block with a
                    # stronger objective contribution.
                    scheduled_competitor = next(
                        (
                            other_id
                            for other_id in scheduled_ids
                            if (
                                by_id[other_id].track_id
                                == block.track_id
                            )
                        ),
                        None,
                    )

                    if scheduled_competitor is not None:
                        competing_block = by_id[
                            scheduled_competitor
                        ]

                        block_score = _objective_score(
                            request,
                            block,
                        )

                        competing_score = _objective_score(
                            request,
                            competing_block,
                        )

                        if block_score < competing_score:
                            reason = (
                                "lower objective value than "
                                "scheduled competing block: "
                                f"{scheduled_competitor}"
                            )
                        else:
                            reason = (
                                "excluded by optimizer: "
                                "capacity/priority trade-off"
                            )
                    else:
                        reason = (
                            "excluded by optimizer: "
                            "capacity/priority trade-off"
                        )

        rejection_reasons[block_id] = reason
        unscheduled_blocks.append(block)

    # ------------------------------------------------------------------
    # Aggregate KPIs represented by the new contract
    # ------------------------------------------------------------------
    total_priority_scheduled = sum(
        block.priority_score
        for block in blocks
        if block.block_id in scheduled_ids
    )

    total_risk_mitigated = sum(
        block.risk_score
        for block in blocks
        if block.block_id in scheduled_ids
    )

    return OptimizationResult(
        corridor_id=request.corridor_id,
        status=status,
        scheduled_blocks=scheduled_blocks,
        unscheduled_blocks=unscheduled_blocks,
        total_priority_scheduled=round(
            total_priority_scheduled,
            3,
        ),
        total_risk_mitigated=round(
            total_risk_mitigated,
            3,
        ),
        solve_time_seconds=solve_time_seconds,
        infeasibility_reasons=[],
        rejection_reasons=rejection_reasons,
    )