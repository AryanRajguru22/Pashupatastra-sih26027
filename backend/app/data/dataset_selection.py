"""Which NDLS-AGC dataset this deployment serves: synthetic fallback or real snapshot.

    PASHUPAT_RAILWAY_DATA   unset / "synthetic"  the checked-in synthetic dataset
                                                 (deterministic fallback, unchanged)
                            "real"               the offline public-railway-data
                                                 snapshot (data/railway/ndls_agc)

Matching is exact; any other value is refused. Nothing ever falls back silently
from "real" to "synthetic": a broken or altered snapshot raises
RealSnapshotError at startup instead of quietly serving invented data under a
real label.

Only the corridor selected by PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC has a dataset
at all; the generated corridors have none, so "real" with a generated corridor is
a configuration error, not a no-op.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

from backend.app.data.corridor_dataset import CorridorDataset, load_corridor_dataset

RAILWAY_DATA_ENV = "PASHUPAT_RAILWAY_DATA"

MODE_SYNTHETIC = "synthetic"
MODE_REAL = "real"


class RailwayDataConfigurationError(ValueError):
    """PASHUPAT_RAILWAY_DATA holds an unsupported value."""


def railway_data_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    raw = env.get(RAILWAY_DATA_ENV)

    if raw is None or raw == MODE_SYNTHETIC:
        return MODE_SYNTHETIC

    if raw == MODE_REAL:
        return MODE_REAL

    raise RailwayDataConfigurationError(
        f"{RAILWAY_DATA_ENV} must be exactly '{MODE_SYNTHETIC}' or '{MODE_REAL}'; got {raw!r}."
    )


def load_configured_dataset(
    environ: Optional[Mapping[str, str]] = None,
) -> CorridorDataset:
    """The dataset the environment selects. Real mode never degrades to synthetic."""

    if railway_data_mode(environ) == MODE_REAL:
        # Imported here so the synthetic path never touches the snapshot code.
        from backend.app.data.real_corridor_dataset import load_real_corridor_dataset

        return load_real_corridor_dataset()

    return load_corridor_dataset()


__all__ = [
    "MODE_REAL",
    "MODE_SYNTHETIC",
    "RAILWAY_DATA_ENV",
    "RailwayDataConfigurationError",
    "load_configured_dataset",
    "railway_data_mode",
]
