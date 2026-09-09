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
    # `from contracts import OptimizationRequest` resolves to the
    # canonical dataclass in contracts/schemas.py, which deserializes
    # with from_dict(). This previously called Pydantic's
    # model_validate() and raised AttributeError on every call - the
    # helper simply had no callers, so nobody noticed. It is the
    # clearest evidence of why contracts/schemas.py must stay the one
    # canonical contract path (see the banner in contracts/common.py).
    raw = json.loads(path.read_text(encoding="utf-8"))
    return OptimizationRequest.from_dict(raw)
