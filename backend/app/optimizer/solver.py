"""CP-SAT block scheduling engine - Milestone 1.

Takes an OptimizationRequest (a fixed set of BlockCandidates with AI/ML
priority/risk scores already attached) and produces a feasible
OptimizationResult using OR-Tools CP-SAT. The optimizer treats
priority_score/risk_score purely as objective-function weights: they
influence *which* feasible schedule is chosen, never whether a hard
safety constraint (track no-overlap, headway, dependency ordering,
crew mutual exclusion, block time windows) is honored.

This module is intentionally standalone - it has no dependency on
FastAPI, the frontend, or a database, so it can be exercised directly
via scripts/run_milestone1.py to prove CP-SAT can solve this problem
shape before the rest of the stack exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pyexpat import model
from tracemalloc import start

from ortools.sat.python import cp_model

from contracts import (
    BlockCandidate,
    ExplanationEntry,
    OptimizationKPIs,
    OptimizationRequest,
    OptimizationResult,
    OptimizationStatus,
    ScheduledBlock,
    UnscheduledBlock,
)

_SCORE_SCALE = 1000
_SOLVE_TIME_LIMIT_SECONDS = 10.0


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def solve(request: OptimizationRequest) -> OptimizationResult:
    horizon_start = request.planning_horizon.start
    blocks: list[BlockCandidate] = request.block_candidates
    by_id = {b.block_id: b for b in blocks}

    model = cp_model.CpModel()

    presence: dict[str, cp_model.IntVar] = {}
    start: dict[str, cp_model.IntVar] = {}
    end: dict[str, cp_model.IntVar] = {}
    window_infeasible: set[str] = set()

    committed_by_id = {
        block.block_id: block
        for block in request.existing_committed_blocks
    }

    for block in blocks:
        earliest = _minutes(block.earliest_start - horizon_start)
        latest = _minutes(block.latest_finish - horizon_start)
        latest_start = latest - block.duration_minutes

        presence_var = model.NewBoolVar(f"presence_{block.block_id}")
        presence[block.block_id] = presence_var

        if latest_start < earliest:
            # Duration cannot possibly fit in [earliest_start, latest_finish).
            # Never schedulable - forced off rather than left to the solver.
            window_infeasible.add(block.block_id)
            model.Add(presence_var == 0)
            latest_start = earliest  # keep the domain well-formed

        start_var = model.NewIntVar(
            earliest,
            latest_start,
            f"start_{block.block_id}"
        )

        end_var = model.NewIntVar(
            earliest,
            latest,
            f"end_{block.block_id}"
        )

        model.Add(end_var == start_var + block.duration_minutes)

        committed = committed_by_id.get(block.block_id)

        if committed is not None:
            committed_start = _minutes(committed.start - horizon_start)
            committed_end = _minutes(committed.end - horizon_start)

            model.Add(presence_var == 1)
            model.Add(start_var == committed_start)
            model.Add(end_var == committed_end)

        start[block.block_id] = start_var
        end[block.block_id] = end_var

    # Track no-overlap, with each interval padded by the configured minimum
    # headway so consecutive blocks on the same track are never back-to-back.
    headway = request.constraints_config.min_headway_minutes
    by_track: dict[str, list[str]] = {}
    for block in blocks:
        by_track.setdefault(block.track_id, []).append(block.block_id)

    for track_id, block_ids in by_track.items():
        buffered_intervals = []
        for block_id in block_ids:
            block = by_id[block_id]
            buffered_size = block.duration_minutes + headway
            buffered_end = model.NewIntVar(
                0, 10**9, f"buffered_end_{block_id}"
            )
            model.Add(buffered_end == start[block_id] + buffered_size)
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

    # Dependencies: a block can only be scheduled if every block it depends
    # on is also scheduled and finishes before it starts.
    for block in blocks:
        for dep_id in block.dependencies:
            if dep_id not in by_id:
                continue  # unknown dependency - ignored in v0.1
            model.Add(presence[block.block_id] <= presence[dep_id])
            model.Add(start[block.block_id] >= end[dep_id]).OnlyEnforceIf(
                presence[block.block_id]
            )

    # Mutual exclusion (e.g. shared crew) across blocks that may sit on
    # different tracks, so ordinary same-track no-overlap doesn't cover them.
    seen_pairs: set[frozenset[str]] = set()
    for block in blocks:
        for other_id in block.mutually_exclusive_with:
            if other_id not in by_id:
                continue
            pair = frozenset((block.block_id, other_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            both_present = model.NewBoolVar(f"both_present_{'_'.join(sorted(pair))}")
            model.AddBoolAnd(
                [presence[block.block_id], presence[other_id]]
            ).OnlyEnforceIf(both_present)
            model.AddBoolOr(
                [presence[block.block_id].Not(), presence[other_id].Not()]
            ).OnlyEnforceIf(both_present.Not())

            order = model.NewBoolVar(f"order_{'_'.join(sorted(pair))}")
            model.Add(end[block.block_id] <= start[other_id]).OnlyEnforceIf(
                [both_present, order]
            )
            model.Add(end[other_id] <= start[block.block_id]).OnlyEnforceIf(
                [both_present, order.Not()]
            )

    weights = request.objective_weights
    objective_terms = []
    for block in blocks:
        coefficient = round(
            _SCORE_SCALE
            * (
                weights.risk_reduction_weight * block.risk_score
                + weights.blocks_completed_weight * block.priority_score
            )
        )
        objective_terms.append(coefficient * presence[block.block_id])
    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVE_TIME_LIMIT_SECONDS
    solve_status = solver.Solve(model)

    status_map = {
        cp_model.OPTIMAL: OptimizationStatus.OPTIMAL,
        cp_model.FEASIBLE: OptimizationStatus.FEASIBLE,
        cp_model.INFEASIBLE: OptimizationStatus.INFEASIBLE,
        cp_model.UNKNOWN: OptimizationStatus.TIMEOUT,
    }
    status = status_map.get(solve_status, OptimizationStatus.INFEASIBLE)

    if status not in (OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE):
        return OptimizationResult(
            request_id=request.request_id,
            status=status,
            scheduled_blocks=[],
            unscheduled_blocks=[
                UnscheduledBlock(block_id=b.block_id, reason="solver found no feasible solution")
                for b in blocks
            ],
            kpis=OptimizationKPIs(
                asset_availability_pct=100.0,
                trains_affected=0,
                blocks_scheduled=0,
                risk_reduction_score=0.0,
            ),
            explainability=[],
            solve_time_ms=int(solver.WallTime() * 1000),
            generated_at=datetime.utcnow(),
        )

    scheduled_blocks: list[ScheduledBlock] = []
    unscheduled_blocks: list[UnscheduledBlock] = []
    explainability: list[ExplanationEntry] = []

    for block in blocks:
        if solver.Value(presence[block.block_id]):
            start_dt = horizon_start + timedelta(minutes=solver.Value(start[block.block_id]))
            end_dt = horizon_start + timedelta(minutes=solver.Value(end[block.block_id]))
            scheduled_blocks.append(
                ScheduledBlock(block_id=block.block_id, track_id=block.track_id, start=start_dt, end=end_dt)
            )
            binding = [f"track_no_overlap:{block.track_id}"]
            binding += [f"depends_on:{dep}" for dep in block.dependencies]
            binding += [f"mutually_exclusive_with:{ex}" for ex in block.mutually_exclusive_with]
            explainability.append(
                ExplanationEntry(
                    block_id=block.block_id,
                    reason=(
                        f"Scheduled on track {block.track_id} {start_dt.isoformat()} - "
                        f"{end_dt.isoformat()} (priority={block.priority_score}, risk={block.risk_score})."
                    ),
                    binding_constraints=binding,
                )
            )
        elif block.block_id in window_infeasible:
            unscheduled_blocks.append(
                UnscheduledBlock(
                    block_id=block.block_id,
                    reason="duration does not fit within earliest_start/latest_finish window",
                )
            )
        else:
            unscheduled_blocks.append(
                UnscheduledBlock(
                    block_id=block.block_id,
                    reason="excluded by optimizer: capacity/priority trade-off on this track",
                )
            )

    horizon_minutes = _minutes(request.planning_horizon.end - horizon_start)
    tracks = set(by_track.keys())
    total_track_minutes = horizon_minutes * max(len(tracks), 1)
    total_blocked_minutes = sum(
        _minutes(sb.end - sb.start) for sb in scheduled_blocks
    )
    asset_availability_pct = (
        100.0 * (1 - total_blocked_minutes / total_track_minutes)
        if total_track_minutes
        else 100.0
    )

    kpis = OptimizationKPIs(
        asset_availability_pct=round(asset_availability_pct, 2),
        trains_affected=0,
        blocks_scheduled=len(scheduled_blocks),
        risk_reduction_score=round(
            sum(by_id[sb.block_id].risk_score for sb in scheduled_blocks), 3
        ),
    )

    return OptimizationResult(
        request_id=request.request_id,
        status=status,
        scheduled_blocks=scheduled_blocks,
        unscheduled_blocks=unscheduled_blocks,
        kpis=kpis,
        explainability=explainability,
        solve_time_ms=int(solver.WallTime() * 1000),
        generated_at=datetime.utcnow(),
    )
