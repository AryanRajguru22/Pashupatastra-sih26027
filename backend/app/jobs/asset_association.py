"""Bounded asset association and asset-reference integrity (Slice 8).

TWO DISTINCT PROBLEMS, ONE MODULE
    1. ASSOCIATION - which asset does a newly reported job attach to?
       Until Slice 8 JobService._nearest_asset returned the nearest asset
       on the track at ANY distance, so a defect on a signal post could be
       filed (and scored, off that asset's criticality, condition and
       failure history) against an OHE mast tens of kilometres away.
       select_asset refuses instead of guessing.

    2. REFERENCE INTEGRITY - can a persisted asset_id still be trusted?
       Assets are not stored. They are regenerated in memory on every
       JobService construction (CorridorDataGenerator(seed=42)), so an
       asset_id written into a job is only a NAME. If the seed, the track
       list or assets_per_track ever changes, the same name silently
       points at a different physical asset. asset_fingerprint and
       verify_asset_reference make that detectable.

THE BOUND - AND WHY IT IS THIS NUMBER
    AssetAssociationPolicy.max_distance_km is measured from the asset to
    the job's declared span [start, end] (0 when the asset lies inside
    it). It is a documented, injectable policy value
    (JobService(asset_policy=...)), never a literal inside business logic.

    The default, DEFAULT_ASSET_ASSOCIATION_MAX_KM, is sized against the
    checked-in synthetic corridor, not against physics: that corridor
    holds 8 assets per track over 195 km (about 24 km apart), so the
    largest distance from any location on it to its nearest same-track
    asset is 18.05 km (DOWN-1, at the corridor origin). Measured on that
    corridor: a 10 km bound leaves 23% of its locations un-reportable, a
    17 km bound 0.6%, and 20 km none. 20 km therefore covers every
    location the checked-in corridor can describe while still refusing an
    association that is not even the same stretch of line. A REAL asset register has
    assets metres apart; when one is wired in this default must be
    tightened by a deliberate decision - it is a plausibility ceiling on a
    sparse synthetic register, not a claim that a defect 20 km from an
    asset is "at" that asset.

SECTIONS
    Tracks are already kept apart (only assets on the job's own track are
    candidates). Sections are NOT filtered by default: the geographically
    nearest asset may sit just across a section boundary, and forcing an
    in-section asset 17 km away would be the worse answer. Instead every
    association records asset_section_id and section_match, so a cross-
    section association is visible in the job and in JOB_CREATED - never
    silent. require_same_section=True turns that record into a refusal
    for deployments that want the strict rule.

WHAT THIS DOES NOT DO
    It is not an asset database and does not solve durable production
    asset identity. The fingerprint detects that a name no longer means
    what it meant; it cannot make a regenerated set durable. Durable
    identity needs an authorised asset register behind an AssetProvider
    seam - a later data-infrastructure slice.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.app.data.models import Asset
from backend.app.data.section_registry import (
    ChainageResolutionError,
    SectionRegistry,
)
from backend.app.jobs.events import canonical_json


DEFAULT_ASSET_ASSOCIATION_MAX_KM = 20.0

# Bumped only if the fields covered by asset_fingerprint change, so an
# old fingerprint is never compared against a new recipe.
ASSET_FINGERPRINT_VERSION = 1

ASSET_RESOLUTION_DERIVED = "DERIVED"

# Distances are compared after rounding to a micrometre-scale grid so a
# value that is exactly on the bound (20.0 - 0.0) is not lost to float
# noise from subtracting two 2-decimal kilometre figures.
_DISTANCE_GRID = 6


class AssetAssociationError(ValueError):
    """No asset may be associated with the job. Fails closed: 400."""


class AssetReferenceError(ValueError):
    """A persisted asset_id cannot be trusted against the active asset
    set. Fails closed: 409."""


@dataclass(frozen=True)
class AssetAssociationPolicy:
    max_distance_km: float = DEFAULT_ASSET_ASSOCIATION_MAX_KM
    require_same_section: bool = False

    def __post_init__(self) -> None:
        value = self.max_distance_km

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(
                "AssetAssociationPolicy.max_distance_km must be a finite "
                f"number >= 0; got {value!r}."
            )

        if not isinstance(self.require_same_section, bool):
            raise ValueError(
                "AssetAssociationPolicy.require_same_section must be a "
                f"bool; got {self.require_same_section!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_distance_km": float(self.max_distance_km),
            "require_same_section": self.require_same_section,
        }


@dataclass(frozen=True)
class AssetAssociation:
    """The asset a job was attached to, and how that was decided."""

    asset: Asset
    distance_km: float
    midpoint_distance_km: float
    asset_section_id: Optional[str]
    job_section_id: str
    section_match: bool
    policy: AssetAssociationPolicy
    corridor_id: str

    def to_metadata(self) -> dict[str, Any]:
        """Plain-JSON record stored on the job and in JOB_CREATED."""

        return {
            "resolution": ASSET_RESOLUTION_DERIVED,
            "asset_id": self.asset.asset_id,
            "distance_km": round(self.distance_km, 3),
            "asset_section_id": self.asset_section_id,
            "job_section_id": self.job_section_id,
            "section_match": self.section_match,
            "policy": self.policy.to_dict(),
            "reference": {
                "corridor_id": self.corridor_id,
                "fingerprint": asset_fingerprint(self.corridor_id, self.asset),
                "fingerprint_version": ASSET_FINGERPRINT_VERSION,
                # The asset SET is generated, so what it says about an
                # asset is SYNTHETIC whoever reported the defect. A
                # genuine human observation attached to it does not
                # promote it - see backend.app.data.provenance.
                "provenance": "SYNTHETIC",
            },
        }


def _usable(asset: Asset) -> bool:
    """An asset with a name and a finite numeric position."""

    km = getattr(asset, "km_location", None)

    return (
        bool(getattr(asset, "asset_id", None))
        and bool(getattr(asset, "track_id", None))
        and isinstance(km, (int, float))
        and not isinstance(km, bool)
        and math.isfinite(km)
    )


def _asset_section_id(
    registry: SectionRegistry,
    asset: Asset,
) -> Optional[str]:
    try:
        return registry.resolve_by_chainage(
            asset.km_location,
            track_id=asset.track_id,
        ).section_id
    except ChainageResolutionError:
        return None


def select_asset(
    assets: Iterable[Asset],
    *,
    corridor_id: str,
    registry: SectionRegistry,
    track_id: str,
    job_section_id: str,
    distance_start_m: float,
    distance_end_m: float,
    policy: AssetAssociationPolicy,
) -> AssetAssociation:
    """The one asset this job may attach to, or a refusal.

    Selection rule (deterministic):
      1. candidates are assets on the job's track with a usable position
         (an asset with no id, no track or a non-finite km is invalid
         data and is never a candidate);
      2. a candidate is admissible only if its distance to the job's span
         is <= policy.max_distance_km (inclusive), and - when
         policy.require_same_section - it lies in the job's section;
      3. among admissible candidates the one nearest the span's MIDPOINT
         wins, exactly the criterion the pre-Slice-8 code used, so a job
         that already had a valid nearby asset keeps the same asset and
         the same score; ties break on (km_location, asset_id).

    Raises AssetAssociationError when nothing is admissible. It never
    falls back to a farther asset, another track, or a made-up one.
    """

    start_km = distance_start_m / 1000.0
    end_km = distance_end_m / 1000.0
    midpoint_km = (start_km + end_km) / 2.0

    on_track = [a for a in assets if getattr(a, "track_id", None) == track_id]

    if not on_track:
        raise AssetAssociationError(
            f"No asset found for track_id '{track_id}'"
        )

    candidates = [a for a in on_track if _usable(a)]

    if not candidates:
        raise AssetAssociationError(
            f"No usable asset for track_id '{track_id}': all "
            f"{len(on_track)} asset record(s) on it are missing an "
            "asset_id or a finite km_location."
        )

    max_km = policy.max_distance_km
    ranked = []
    nearest_span_km: Optional[float] = None
    same_section_rejected = 0

    for asset in candidates:
        km = float(asset.km_location)
        span_distance = round(
            max(0.0, start_km - km, km - end_km),
            _DISTANCE_GRID,
        )

        if nearest_span_km is None or span_distance < nearest_span_km:
            nearest_span_km = span_distance

        if span_distance > max_km:
            continue

        asset_section = _asset_section_id(registry, asset)
        section_match = asset_section == job_section_id

        if policy.require_same_section and not section_match:
            same_section_rejected += 1
            continue

        ranked.append(
            (
                round(abs(km - midpoint_km), _DISTANCE_GRID),
                km,
                asset.asset_id,
                asset,
                span_distance,
                asset_section,
                section_match,
            )
        )

    if not ranked:
        reason = (
            f"No asset on track '{track_id}' lies within "
            f"{max_km} km of the job's span "
            f"{start_km}-{end_km} km (nearest is "
            f"{nearest_span_km} km away)"
        )

        if policy.require_same_section and same_section_rejected:
            reason += (
                f"; {same_section_rejected} asset(s) within the bound were "
                f"outside section '{job_section_id}' and "
                "require_same_section is set"
            )

        raise AssetAssociationError(
            reason
            + ". The report is refused rather than attached to an "
            "implausibly distant asset."
        )

    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    (
        midpoint_distance,
        _,
        _,
        asset,
        span_distance,
        asset_section,
        section_match,
    ) = ranked[0]

    return AssetAssociation(
        asset=asset,
        distance_km=span_distance,
        midpoint_distance_km=midpoint_distance,
        asset_section_id=asset_section,
        job_section_id=job_section_id,
        section_match=section_match,
        policy=policy,
        corridor_id=corridor_id,
    )


# ----------------------------------------------------------------------
# Reference integrity
# ----------------------------------------------------------------------


def asset_fingerprint(corridor_id: str, asset: Asset) -> str:
    """A digest of what makes an asset THIS asset, not what it looks like.

    Covers the corridor, the id and the fields that say WHERE and WHAT it
    is (track, type, position). Deliberately excludes condition, defect
    severity and maintenance age: those change over an asset's life and
    must not make a legitimate reference look stale.
    """

    return hashlib.sha256(
        canonical_json(
            {
                "v": ASSET_FINGERPRINT_VERSION,
                "corridor_id": corridor_id,
                "asset_id": asset.asset_id,
                "track_id": asset.track_id,
                "asset_type": str(asset.asset_type),
                "km_location": round(float(asset.km_location), 3),
            }
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class AssetReferenceCheck:
    """A reference that passed. status says how strongly."""

    asset_id: str
    asset: Asset
    # VERIFIED: id resolves in the active set AND the recorded
    # fingerprint matches. RESOLVED_UNFINGERPRINTED: id resolves but the
    # job predates fingerprints, so only existence could be checked.
    status: str


ASSET_REFERENCE_VERIFIED = "VERIFIED"
ASSET_REFERENCE_UNFINGERPRINTED = "RESOLVED_UNFINGERPRINTED"


def verify_asset_reference(
    active_corridor_id: str,
    active_assets: Sequence[Asset],
    asset_id: Optional[str],
    *,
    recorded_corridor_id: Optional[str] = None,
    recorded_fingerprint: Optional[str] = None,
) -> AssetReferenceCheck:
    """Validate a persisted asset_id against the ACTIVE asset set.

    Fails closed (AssetReferenceError) when the id is blank, does not
    exist in the active set, is ambiguous (two assets carry it), was
    recorded against a different corridor, or its recorded fingerprint no
    longer matches the asset the id now names - the "same id, different
    physical asset" case. It never substitutes a different asset.
    """

    if not asset_id or not isinstance(asset_id, str):
        raise AssetReferenceError(
            "The job carries no asset_id, so there is no asset reference "
            "to trust."
        )

    if recorded_corridor_id is not None and recorded_corridor_id != active_corridor_id:
        raise AssetReferenceError(
            f"asset_id {asset_id!r} was recorded against corridor "
            f"{recorded_corridor_id!r} but the active corridor is "
            f"{active_corridor_id!r}; an id is only meaningful inside the "
            "asset set that issued it."
        )

    matches = [a for a in active_assets if a.asset_id == asset_id]

    if not matches:
        raise AssetReferenceError(
            f"asset_id {asset_id!r} does not exist in the active asset set "
            f"of corridor {active_corridor_id!r}. The reference is stale or "
            "was issued by a different asset set; it is not resolved to any "
            "other asset."
        )

    if len(matches) > 1:
        raise AssetReferenceError(
            f"asset_id {asset_id!r} is ambiguous: {len(matches)} assets in "
            "the active set carry it."
        )

    asset = matches[0]

    if recorded_fingerprint is None:
        return AssetReferenceCheck(
            asset_id=asset_id,
            asset=asset,
            status=ASSET_REFERENCE_UNFINGERPRINTED,
        )

    if recorded_fingerprint != asset_fingerprint(active_corridor_id, asset):
        raise AssetReferenceError(
            f"asset_id {asset_id!r} exists in the active asset set but no "
            "longer describes the asset it was recorded against (its track, "
            "type or position differ). The asset set has changed under a "
            "stored identifier; the reference is refused rather than "
            "silently re-pointed."
        )

    return AssetReferenceCheck(
        asset_id=asset_id,
        asset=asset,
        status=ASSET_REFERENCE_VERIFIED,
    )


def reference_from_block(
    block_dict: Mapping[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(asset_id, recorded_corridor_id, recorded_fingerprint) of a stored
    block_candidate dict. Missing pieces come back None, never guessed."""

    metadata = block_dict.get("metadata") or {}
    association = metadata.get("asset_association") or {}
    reference = association.get("reference") or {}

    return (
        block_dict.get("asset_id"),
        reference.get("corridor_id"),
        reference.get("fingerprint"),
    )


__all__ = [
    "ASSET_FINGERPRINT_VERSION",
    "ASSET_REFERENCE_UNFINGERPRINTED",
    "ASSET_REFERENCE_VERIFIED",
    "ASSET_RESOLUTION_DERIVED",
    "DEFAULT_ASSET_ASSOCIATION_MAX_KM",
    "AssetAssociation",
    "AssetAssociationError",
    "AssetAssociationPolicy",
    "AssetReferenceCheck",
    "AssetReferenceError",
    "asset_fingerprint",
    "reference_from_block",
    "select_asset",
    "verify_asset_reference",
]
