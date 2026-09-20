"""Railway scope: a corridor and an explicit, finite set of its sections (Slice 10.1C).

    RailwayScope(corridor_id, section_ids)

A scope is plain immutable data: "these sections of this corridor". It is
NOT a role, NOT a permission, NOT part of Person, Actor or
IdentityAssurance, and confers nothing by itself. It is attached to a role
assignment by scope_assignment.py; matching a resource against it is
resource.py. Nothing here authorizes, authenticates or persists anything.

EXPLICIT MEANS EXPLICIT
    There is no hierarchy, no wildcard, no "all sections" marker, no
    implicit corridor-wide reach, no track or asset scope, and no
    inference of adjacent sections. Every covered section is named. An
    empty section set is refused: it must never be read as "the whole
    corridor". "*" and "ALL" are refused as section ids. Because the ids
    are stored, a section added to a corridor's topology later is NOT
    covered by an existing scope.

IDENTITY
    Section ids are opaque, case-sensitive strings kept exactly as given
    (never stripped, re-cased or parsed): the topology's own convention is
    that a section_id is an opaque key, unique only within its corridor.
    The scope therefore carries corridor_id too, and a resource's identity
    is always the PAIR (corridor_id, section_id). Section ids are held in a
    frozenset, so {S3, S2} and {S2, S3} are the same scope and compare and
    hash equal. A sequence containing the same id twice is refused rather
    than silently collapsed; a set cannot carry a duplicate at all.

TWO STEPS OF VALIDATION
    RailwayScope(...) checks structure only (it has no topology to consult).
    railway_scope(directory, ...) additionally proves against authoritative
    topology that the directory describes the named corridor and that every
    section id is registered in it. The directory is injected read-only via
    the SectionDirectory Protocol (backend.app.data.section_registry.
    SectionRegistry satisfies it); this module builds and fetches no
    topology and stores none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, Protocol, Tuple, runtime_checkable

INVALID_CORRIDOR = "INVALID_CORRIDOR"
INVALID_SECTION = "INVALID_SECTION"
EMPTY_SCOPE = "EMPTY_SCOPE"
WILDCARD_SECTION = "WILDCARD_SECTION"
DUPLICATE_SECTION = "DUPLICATE_SECTION"
UNKNOWN_SECTION = "UNKNOWN_SECTION"


class ScopeError(ValueError):
    """A railway scope was refused; see .reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


@runtime_checkable
class SectionDirectory(Protocol):
    """Read-only topology: which sections exist in one corridor."""

    corridor_id: str

    def has(self, section_id: str) -> bool: ...


def _is_wildcard(value: str) -> bool:
    return "*" in value or value.strip().upper() == "ALL"


def _check_identifier(value: object, reason: str, label: str) -> str:
    """Return value unchanged, or raise. Never strips or re-cases it."""

    if not isinstance(value, str):
        raise ScopeError(reason, f"{label} must be a string; got {type(value).__name__}.")

    if _is_wildcard(value):
        raise ScopeError(
            WILDCARD_SECTION if reason == INVALID_SECTION else reason,
            f"{label} {value!r} is a wildcard or 'all' marker; scope must "
            "name explicit ids.",
        )

    if not value.strip():
        raise ScopeError(reason, f"{label} must not be empty or whitespace only.")

    if value != value.strip():
        raise ScopeError(
            reason,
            f"{label} {value!r} has leading or trailing whitespace; it is "
            "rejected rather than silently changed.",
        )

    if any(not ch.isprintable() for ch in value):
        raise ScopeError(reason, f"{label} {value!r} contains control characters.")

    return value


def _explicit_sections(section_ids: object) -> FrozenSet[str]:
    """Validate a collection of section ids into a non-empty frozenset."""

    if isinstance(section_ids, (str, bytes)) or not isinstance(
        section_ids, Iterable
    ) or hasattr(section_ids, "keys"):
        raise ScopeError(
            INVALID_SECTION,
            "section_ids must be a collection of section id strings; got "
            f"{type(section_ids).__name__}.",
        )

    seen = set()
    for member in section_ids:
        section_id = _check_identifier(member, INVALID_SECTION, "section_id")
        if section_id in seen:
            raise ScopeError(
                DUPLICATE_SECTION,
                f"section_id {section_id!r} is listed more than once.",
            )
        seen.add(section_id)

    if not seen:
        raise ScopeError(
            EMPTY_SCOPE,
            "A scope must name at least one section; an empty set is never "
            "'the whole corridor'.",
        )

    return frozenset(seen)


@dataclass(frozen=True)
class RailwayScope:
    """The explicit sections of one corridor. No wildcard, no hierarchy."""

    corridor_id: str
    section_ids: FrozenSet[str]

    def __post_init__(self) -> None:
        _check_identifier(self.corridor_id, INVALID_CORRIDOR, "corridor_id")

        object.__setattr__(self, "section_ids", _explicit_sections(self.section_ids))

    def sorted_section_ids(self) -> Tuple[str, ...]:
        """The section ids in plain string order. Determinism only, no ranking."""

        return tuple(sorted(self.section_ids))


def railway_scope(
    directory: SectionDirectory,
    corridor_id: str,
    section_ids: Iterable[str],
) -> RailwayScope:
    """Build a scope proven against authoritative topology.

    Refuses a corridor the directory does not describe (so a directory for
    corridor B can never validate a scope labelled corridor A merely
    because it happens to know the same section string), and any section
    id the directory does not register.
    """

    scope = RailwayScope(corridor_id, section_ids)

    directory_corridor: Any = getattr(directory, "corridor_id", None)
    if directory_corridor != scope.corridor_id:
        raise ScopeError(
            INVALID_CORRIDOR,
            f"The topology describes corridor {directory_corridor!r}, not "
            f"{scope.corridor_id!r}.",
        )

    for section_id in scope.sorted_section_ids():
        if not directory.has(section_id):
            raise ScopeError(
                UNKNOWN_SECTION,
                f"section_id {section_id!r} is not a section of corridor "
                f"{scope.corridor_id!r}.",
            )

    return scope


__all__ = [
    "RailwayScope",
    "ScopeError",
    "SectionDirectory",
    "railway_scope",
]
