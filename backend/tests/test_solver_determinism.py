"""Reproducibility of the CP-SAT solver.

SCOPE OF THE GUARANTEE (see backend/app/optimizer/solver.py):

    Guaranteed - identical request produces an identical schedule for the
    same process, machine, OR-Tools version and solver configuration,
    provided the solve completes inside the configured time limit.

    Not guaranteed - stability across OR-Tools versions, or when the
    wall-clock time limit is actually hit.

Why it was not reproducible before: CP-SAT defaults to
num_search_workers=0 ("auto"), which runs a parallel portfolio. The
objective rewards only WHETHER a block is scheduled, never WHERE it is
placed, so many placements are equally optimal and whichever worker
finished first decided the answer. Measured on corridor_b_dense: 28
distinct schedules across 30 identical solves. random_seed was already
1 by default and was never the cause.
"""

from __future__ import annotations

import json

import pytest

from backend.app.optimizer.solver import (
    _RANDOM_SEED,
    _SEARCH_WORKERS,
    solve,
)
from contracts import OptimizationRequest


FIXTURES = [
    "corridor_a_blocks.json",
    "corridor_b_dense.json",
    "corridor_c_disrupted.json",
]

REPEATS = 12


def load(fixture_name: str) -> OptimizationRequest:
    path = f"backend/app/data/fixtures/{fixture_name}"

    with open(path, encoding="utf-8") as handle:
        return OptimizationRequest.from_dict(json.load(handle))


def signature(result) -> tuple:
    """Full placement signature - not just which blocks were scheduled.

    Comparing only the scheduled block ids would have passed even while
    the solver was returning different start times every run.
    """

    return tuple(
        sorted(
            (
                block.block_id,
                block.track_id,
                block.start_minute,
                block.end_minute,
            )
            for block in result.scheduled_blocks
        )
    )


def test_solver_is_configured_for_single_threaded_search():
    """Pin the configuration the guarantee actually depends on.

    If someone restores parallel search, this fails immediately and
    points at the reason, rather than leaving a 1-in-N flaky test
    somewhere else in the suite.
    """

    assert _SEARCH_WORKERS == 1
    assert _RANDOM_SEED == 1


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_repeated_solves_produce_an_identical_schedule(fixture_name: str):
    """The regression that the old single-fixture test kept missing.

    corridor_b_dense is the important case: under the previous default
    configuration it produced a different schedule on almost every run.
    """

    request = load(fixture_name)

    signatures = {signature(solve(request)) for _ in range(REPEATS)}

    assert len(signatures) == 1, (
        f"{fixture_name}: {len(signatures)} distinct schedules across "
        f"{REPEATS} identical solves"
    )


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_repeated_solves_agree_on_reported_totals(fixture_name: str):
    """Reported KPIs must be stable too, not only the placements."""

    request = load(fixture_name)

    outcomes = {
        (
            result.status,
            round(result.total_priority_scheduled, 6),
            round(result.total_risk_mitigated, 6),
            len(result.scheduled_blocks),
            len(result.unscheduled_blocks),
        )
        for result in (solve(request) for _ in range(REPEATS))
    }

    assert len(outcomes) == 1


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_rejection_reasons_are_stable(fixture_name: str):
    """An audit trail that changes between identical runs is not an
    audit trail."""

    request = load(fixture_name)

    reasons = {
        tuple(sorted(solve(request).rejection_reasons.items()))
        for _ in range(REPEATS)
    }

    assert len(reasons) == 1


def test_a_fresh_request_object_solves_identically():
    """Reproducibility must not depend on reusing the same Python
    object - equal input, equal output."""

    raw = json.load(
        open(
            "backend/app/data/fixtures/corridor_b_dense.json",
            encoding="utf-8",
        )
    )

    first = signature(solve(OptimizationRequest.from_dict(raw)))
    second = signature(solve(OptimizationRequest.from_dict(raw)))

    assert first == second
