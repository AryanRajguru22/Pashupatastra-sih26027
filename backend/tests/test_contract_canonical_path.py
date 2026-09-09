"""There must be exactly one canonical frontend <-> backend contract.

The repository contains two contract definition sets:

  contracts/schemas.py            dataclasses, integer minutes  CANONICAL
  contracts/common.py + friends   Pydantic models, datetimes    DORMANT

They are not interchangeable. These tests pin the canonical path so a
future import cannot quietly start resolving to the dormant set, and so
the dormant set cannot quietly acquire runtime callers.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

import contracts
from backend.app.api.deps import load_optimization_request


REPO_ROOT = Path(__file__).resolve().parents[2]

DORMANT_MODULES = {
    "contracts.common",
    "contracts.block_candidate",
    "contracts.optimization_request",
    "contracts.optimization_result",
    "contracts.disruption_event",
}

CANONICAL_NAMES = [
    "BlockCandidate",
    "OptimizationRequest",
    "OptimizationResult",
    "PossessionWindow",
    "ScheduledBlock",
    "DisruptionEvent",
    "RecoveryRequest",
    "RecoveryResponse",
]


@pytest.mark.parametrize("name", CANONICAL_NAMES)
def test_public_contract_names_resolve_to_schemas_dataclasses(name: str):
    """`from contracts import X` must always be the schemas.py version."""

    obj = getattr(contracts, name)

    assert obj.__module__ == "contracts.schemas", (
        f"contracts.{name} resolves to {obj.__module__}, not the "
        "canonical contracts.schemas"
    )
    assert dataclasses.is_dataclass(obj), (
        f"contracts.{name} is not a dataclass - the canonical contract "
        "set is dataclass-based; a Pydantic model here means the "
        "dormant set has leaked into the canonical path"
    )


# RecoveryResponse is serialize-only by design: the backend builds it,
# the frontend consumes it, and nothing deserializes it back in Python.
DESERIALIZED_NAMES = [
    name for name in CANONICAL_NAMES if name != "RecoveryResponse"
]


def test_canonical_contracts_round_trip_through_dicts():
    """The canonical contract is dict-based, not model_validate-based."""

    for name in CANONICAL_NAMES:
        obj = getattr(contracts, name)

        assert hasattr(obj, "to_dict"), f"{name} lacks to_dict"

        assert not hasattr(obj, "model_validate"), (
            f"{name} exposes model_validate - it is a Pydantic model, "
            "so the dormant contract set is being re-exported"
        )

    for name in DESERIALIZED_NAMES:
        assert hasattr(getattr(contracts, name), "from_dict"), (
            f"{name} lacks from_dict"
        )


def _runtime_python_files() -> list[Path]:
    """Application source only - tests and the contracts package itself
    are excluded."""

    files: list[Path] = []

    for path in (REPO_ROOT / "backend" / "app").rglob("*.py"):
        if "tests" in path.parts:
            continue
        files.append(path)

    return files


def test_no_runtime_module_imports_the_dormant_contract_set():
    """The dormancy claim is enforced, not just documented."""

    offenders: list[str] = []

    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in DORMANT_MODULES:
                    offenders.append(f"{path}: from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in DORMANT_MODULES:
                        offenders.append(f"{path}: import {alias.name}")

    assert not offenders, (
        "dormant contract modules must have no runtime importers:\n"
        + "\n".join(offenders)
    )


def test_deps_loader_actually_works():
    """Regression: this helper called Pydantic's model_validate() on a
    dataclass and raised AttributeError on every invocation."""

    path = (
        REPO_ROOT
        / "backend"
        / "app"
        / "data"
        / "fixtures"
        / "corridor_a_blocks.json"
    )

    request = load_optimization_request(path)

    assert isinstance(request, contracts.OptimizationRequest)
    assert request.corridor_id == "CORRIDOR_A"
    assert request.candidates


def test_frontend_type_mirror_matches_the_canonical_contract():
    """frontend/src/types/contracts.ts mirrors contracts/schemas.py.

    Checks the fields the dashboard actually reads, so a rename on the
    backend cannot silently desynchronise the frontend.
    """

    mirror = (
        REPO_ROOT / "frontend" / "src" / "types" / "contracts.ts"
    ).read_text(encoding="utf-8")

    fixture = json.loads(
        (
            REPO_ROOT
            / "backend"
            / "app"
            / "data"
            / "fixtures"
            / "corridor_a_blocks.json"
        ).read_text(encoding="utf-8")
    )

    request = contracts.OptimizationRequest.from_dict(fixture)

    for field in dataclasses.fields(request):
        # The mirror marks optional fields `name?:`, so accept both.
        assert (
            f"{field.name}:" in mirror or f"{field.name}?:" in mirror
        ), (
            f"OptimizationRequest.{field.name} is missing from the "
            "frontend contract mirror"
        )

    result = contracts.OptimizationResult(
        corridor_id="X",
        status="OPTIMAL",
    )

    for field in dataclasses.fields(result):
        assert (
            f"{field.name}:" in mirror or f"{field.name}?:" in mirror
        ), (
            f"OptimizationResult.{field.name} is missing from the "
            "frontend contract mirror"
        )
