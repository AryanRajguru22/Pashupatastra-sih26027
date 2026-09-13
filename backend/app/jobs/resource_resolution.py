"""Resolve a maintenance job's declared physical location to a canonical
solver resource (Sprint 3 Step 11).

THE GAP THIS CLOSES
    Every maintenance job JobService.create_job persists carries a
    track_id but, until this module existed, no section_id: the
    resulting BlockCandidate had section_id=None. The solver's
    canonical resource identity is (track_id, section_id) - see
    backend.app.optimizer.solver._resource_key - but for section_id=None
    it falls back to matching a possession window by track_id ALONE
    (solver._possession_window_covers_block). On the single-section
    synthetic corridors that fallback was harmless: a track had exactly
    one section, so "any window on this track" and "the window for this
    job's section" were the same statement. On the checked-in dataset
    corridor (six sections per track, wired in Step 10) they are not: a
    job at km 5 could be "covered" by a possession window 185 km away.

    This module is the ONE place a job's distance_start/distance_end
    (meters, JobCreateRequest) become a resolved (track_id, section_id)
    pair. JobService.create_job is the only caller - "the earliest safe
    domain boundary where corridor, asset location, track assignment
    are all known", per the Step 11 brief - so section_id is never
    re-derived downstream.

WHAT THIS MODULE DOES NOT DO
    It does not invent chainage, stations, or topology. It does not
    infer a location from a train timetable, the nearest station, the
    corridor midpoint, an asset id, or a track name - every one of those
    is an explicitly forbidden shortcut per the Step 11 brief. A job
    whose declared location cannot be safely resolved through
    SectionRegistry.resolve_by_chainage is REJECTED, with the reason
    stated plainly, never silently defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.data.section_registry import (
    ChainageResolutionError,
    SectionRegistry,
)


class JobResourceResolutionError(ValueError):
    """A job's declared location could not be resolved to one section.

    Deliberately a subclass of ValueError: JobService.create_job's
    existing guards (unknown track_id, no duration model, no nearby
    asset) already raise plain ValueError and are turned into HTTP 400
    by backend.app.jobs.router.create_job's existing except clause. This
    reuses that same path rather than adding a new one.
    """


@dataclass(frozen=True)
class JobResource:
    """The canonical (track_id, section_id) resource a job resolved to."""

    track_id: str
    section_id: str


def resolve_job_resource(
    registry: SectionRegistry,
    track_id: str,
    distance_start_m: float,
    distance_end_m: float,
) -> JobResource:
    """Resolve a job's declared span to the ONE section that contains it.

    distance_start_m / distance_end_m are meters (JobCreateRequest's own
    units - see JobService._nearest_asset, which already divides by
    1000.0 to get a corridor-absolute km for asset lookup). Both
    endpoints are resolved independently through
    SectionRegistry.resolve_by_chainage and must land in the SAME
    section - not merely their midpoint, which is what
    JobService._nearest_asset uses for the (unrelated) job-nearest-asset
    lookup. Requiring both endpoints keeps this resolver from silently
    approving a job whose declared work physically straddles a section
    boundary: a job spanning two sections is a real safety concern
    (possession coverage differs per section) and is rejected outright
    rather than assigned to whichever section its midpoint happens to
    fall in.

    track_id is passed through to resolve_by_chainage on BOTH ends, so
    a job's declared track is validated against section membership at
    the same time its location is resolved - a job whose track does not
    serve the section its location falls in is rejected here, not
    scheduled against a track that cannot reach that stretch of line.

    Raises JobResourceResolutionError (never returns a partial or
    best-guess result) when:
      - either endpoint's chainage cannot be resolved at all (outside
        the corridor, or the registry has no sections at all);
      - either endpoint's track_id does not serve the section at that
        chainage;
      - the two endpoints resolve to DIFFERENT sections (the job's
        declared range crosses a section boundary).
    """

    start_km = distance_start_m / 1000.0
    end_km = distance_end_m / 1000.0

    try:
        start_section = registry.resolve_by_chainage(
            start_km,
            track_id=track_id,
        )
    except ChainageResolutionError as exc:
        raise JobResourceResolutionError(
            f"Cannot resolve the job's start location "
            f"({distance_start_m} m = {start_km} km) on track "
            f"{track_id!r}: {exc}"
        ) from exc

    try:
        end_section = registry.resolve_by_chainage(
            end_km,
            track_id=track_id,
        )
    except ChainageResolutionError as exc:
        raise JobResourceResolutionError(
            f"Cannot resolve the job's end location "
            f"({distance_end_m} m = {end_km} km) on track "
            f"{track_id!r}: {exc}"
        ) from exc

    if start_section.section_id != end_section.section_id:
        raise JobResourceResolutionError(
            f"Job spans {distance_start_m}-{distance_end_m} m "
            f"({start_km}-{end_km} km on track {track_id!r}), which "
            f"crosses a section boundary: the start resolves to section "
            f"{start_section.section_id!r} and the end resolves to "
            f"section {end_section.section_id!r}. A job must be wholly "
            "contained within one section - re-scope the job's declared "
            "range rather than assigning it to either section by guess."
        )

    return JobResource(
        track_id=track_id,
        section_id=start_section.section_id,
    )


__all__ = [
    "JobResource",
    "JobResourceResolutionError",
    "resolve_job_resource",
]
