"""Pure scope/resource matching (Slice 10.1C). Not authorization.

    match_scope(scope, location) -> ScopeMatch

answers one question: does this resource's (corridor_id, section_id)
belong to this explicit RailwayScope? The answer is three-valued and
fails closed:

    MATCH       resolved; same corridor; section is in the scope's set
    NO_MATCH    resolved, but another corridor or a section outside the set
    UNRESOLVED  the resource cannot be reduced to one (corridor, section)

Only MATCH means "inside the scope". UNRESOLVED is never a grant: an
unknown resource scope is not an authorized one. The result is an enum,
never a bool, so it cannot be read as truthy by accident.

RESOURCE IDENTITY
    A resource is located by the PAIR (corridor_id, section_id); a section
    string equal to one in the scope but in another corridor is NO_MATCH.
    Comparison is exact string equality: no case folding, stripping,
    adjacency, ranking or corridor-wide inference. Track and asset play no
    part: neither is a scope unit, and an Asset carries no section, so an
    Asset is UNRESOLVED (its track or km never becomes a section).

MULTI-SECTION RESOURCES
    A resource that spans several sections is NOT matched by first, any or
    all of its sections: it is UNRESOLVED. Callers wanting to act on such a
    resource must resolve it to single-section pieces and match each. This
    is deliberately stricter than the jobs pipeline (resolve_job_resource
    already refuses a job straddling sections) because later resources
    need not carry that guarantee. A missing, blank or non-string
    section or corridor is likewise UNRESOLVED, including the legacy job
    row whose block_candidate has no section_id key.

NO SCOPE
    A scope of None (an ADMIN, or an unassigned person) covers nothing:
    NO_MATCH. ADMIN has no scope; absence is never a wildcard.

location_of adapts the existing shapes (BlockCandidate, BlockProposal,
PossessionWindow, ScheduledBlock, a job record, an Asset) by reading
fields duck-typed; it imports none of them and never reads persistence.
Some of them (PossessionWindow, ScheduledBlock, job rows, Asset) carry no
corridor_id, so the caller supplies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from backend.app.identity.scope import RailwayScope, ScopeError

CORRIDOR_CONFLICT = "CORRIDOR_CONFLICT"


class ScopeMatch(Enum):
    """Fail-closed result of matching a resource against a scope."""

    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ResourceLocation:
    """Where a resource is: a corridor and ONE section, either possibly unknown."""

    corridor_id: Optional[str]
    section_id: Optional[str]


def _usable(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _clean(value: object) -> Optional[str]:
    return value if _usable(value) else None  # type: ignore[return-value]


def _get(source: object, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _section_of(resource: object) -> Optional[str]:
    source = resource

    if isinstance(resource, Mapping) and "block_candidate" in resource:
        # A job record: its block candidate is the single section key.
        source = resource["block_candidate"]
        if not isinstance(source, Mapping):
            return None

    single = _get(source, "section_id")
    plural = _get(source, "section_ids")

    if plural is None:
        return _clean(single)

    if isinstance(plural, (str, bytes)):
        return None

    try:
        distinct = set(plural)
    except TypeError:
        return None

    if len(distinct) != 1:
        return None

    (only,) = distinct

    if single is not None and single != only:
        return None

    return _clean(only)


def location_of(resource: object, corridor_id: Optional[str] = None) -> ResourceLocation:
    """Read a resource's (corridor_id, section_id); unknowns become None.

    corridor_id is the caller's context for resources that carry none. If
    the resource carries its own and the caller supplies a different one,
    the contradiction is refused rather than resolved either way.
    """

    section_id = _section_of(resource)

    own = _get(resource, "corridor_id")
    supplied = _clean(corridor_id)

    if own is None:
        return ResourceLocation(supplied, section_id)

    own_clean = _clean(own)
    if own_clean is None:
        return ResourceLocation(None, section_id)

    if supplied is not None and supplied != own_clean:
        raise ScopeError(
            CORRIDOR_CONFLICT,
            f"The resource is in corridor {own_clean!r} but corridor "
            f"{supplied!r} was supplied.",
        )

    return ResourceLocation(own_clean, section_id)


def match_scope(scope: Optional[RailwayScope], location: ResourceLocation) -> ScopeMatch:
    """Match a located resource against a scope. See the module docstring."""

    if not isinstance(location, ResourceLocation):
        raise TypeError(
            f"location must be a ResourceLocation; got {type(location).__name__}."
        )

    if scope is None:
        return ScopeMatch.NO_MATCH

    if not isinstance(scope, RailwayScope):
        raise TypeError(
            f"scope must be a RailwayScope or None; got {type(scope).__name__}."
        )

    if not _usable(location.corridor_id) or not _usable(location.section_id):
        return ScopeMatch.UNRESOLVED

    if location.corridor_id != scope.corridor_id:
        return ScopeMatch.NO_MATCH

    if location.section_id in scope.section_ids:
        return ScopeMatch.MATCH

    return ScopeMatch.NO_MATCH


__all__ = [
    "ResourceLocation",
    "ScopeMatch",
    "location_of",
    "match_scope",
]
