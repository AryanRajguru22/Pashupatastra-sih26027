"""Small shared plumbing helpers for the API layer.

Not domain modeling - just the fixture-loading pattern already used by
scripts/run_milestone1.py and backend/tests/test_api.py, given one
canonical home so future routers/scripts don't each reinvent it.
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import OptimizationRequest


def load_optimization_request(path: Path) -> OptimizationRequest:
    raw = json.loads(path.read_text())
    return OptimizationRequest.model_validate(raw)
