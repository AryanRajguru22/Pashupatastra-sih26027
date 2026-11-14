"""A directory kept current from NovaForge, feeding the existing enforcing policy.

    NovaForge assignments  --(sync, after a verified liveness check)-->
    NovaForgeDirectory --> immutable IdentityDirectory snapshot --> EnforcingPolicy

NovaForge is the system of record for WHO holds WHICH application role (and,
through resource-limited assignments, over WHICH part of the corridor).
Pashupatastra stays the enforcing party: it still decides every request
through its own role/action table and railway-scope rules
(backend.app.identity.policy); this module only supplies the directory those
rules consult.

Mapping (everything not listed here is ignored, i.e. denied):

  NovaForge role key   -> ActorRole via the configured role map; an unknown
                          role key grants nothing. NovaForge's own platform
                          role (SUPER_ADMIN ...) never appears here at all.
  assignment resource  -> railway scope:
        "*", "corridor/<id>", "corridor/<id>/*"   every section of the corridor
        "corridor/<id>/<section>"                 that one section
      a resource naming another corridor, or a section the topology does not
      contain, contributes nothing. ADMIN never receives scope.
  assignment perms     -> the permission set a request must also hold
                          (see permission_key / NovaForgePolicy)

A person with no mappable role is REMOVED from the directory, so their next
request is refused by the existing session machinery
(PERSON_NOT_ENROLLED destroys the session).

person_id is a stable, opaque value derived from the subject:
"nf-" + the first 24 hex digits of SHA-256("novaforge|" + subject). It carries
no email or name, is a legal actor_id, and is the same after a restart, so the
persistent audit trail keeps attributing to the same person.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from backend.app.identity.actor import ActorRole
from backend.app.identity.assignments import RoleAssignment
from backend.app.identity.authorization import JobAction
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment


def permission_key(action: JobAction) -> str:
    """The NovaForge permission naming an action: JobAction.COMMIT_BLOCK -> job.commit_block."""

    return f"job.{action.value.lower()}"


def person_id_for(subject: str) -> str:
    digest = hashlib.sha256(f"novaforge|{subject}".encode("utf-8")).hexdigest()
    return f"nf-{digest[:24]}"


@dataclass(frozen=True)
class _Grant:
    role: ActorRole
    sections: FrozenSet[str]
    permissions: FrozenSet[str]


class _ScopeView:
    """What the policy holds: always asks the CURRENT snapshot."""

    def __init__(self, owner: "NovaForgeDirectory") -> None:
        self._owner = owner

    def scopes_for_actor(self, actor_id: str, role: ActorRole):
        return self._owner.current().scopes_for_actor(actor_id, role)


class NovaForgeDirectory:
    def __init__(
        self,
        *,
        identity_provider: str,
        corridor_id: str,
        section_ids: Iterable[str],
        role_map: Mapping[str, ActorRole],
    ) -> None:
        self._provider = identity_provider
        self._corridor = corridor_id
        self._sections = frozenset(section_ids)
        self._role_map = dict(role_map)
        self._lock = threading.Lock()
        self._grants: Dict[str, Tuple[Person, Tuple[_Grant, ...]]] = {}
        self._snapshot = IdentityDirectory()
        self._permissions: Dict[Tuple[str, ActorRole], FrozenSet[str]] = {}

    # -- read side -------------------------------------------------------

    def current(self) -> IdentityDirectory:
        return self._snapshot

    def scope_view(self) -> _ScopeView:
        return _ScopeView(self)

    def permissions_for(self, person_id: str, role: ActorRole) -> Optional[FrozenSet[str]]:
        """The permissions NovaForge grants this person under this role, or None."""

        return self._permissions.get((person_id, role))

    def roles_for(self, person_id: str) -> Tuple[ActorRole, ...]:
        return tuple(sorted({r for (pid, r) in self._permissions if pid == person_id}, key=lambda r: r.value))

    # -- write side (only called after a verified NovaForge answer) -------

    def sync(self, subject: str, assignments: Any) -> Tuple[str, Tuple[ActorRole, ...]]:
        """Replace this subject's grants. Returns (person_id, roles now held)."""

        person = Person(person_id_for(subject), ExternalIdentity(self._provider, subject))
        grants = self._map(assignments)
        with self._lock:
            if grants:
                self._grants[person.person_id] = (person, grants)
            else:
                self._grants.pop(person.person_id, None)
            self._rebuild()
        return person.person_id, tuple(sorted({g.role for g in grants}, key=lambda r: r.value))

    def remove(self, subject: str) -> None:
        with self._lock:
            self._grants.pop(person_id_for(subject), None)
            self._rebuild()

    # -- internals -------------------------------------------------------

    def _sections_for(self, resource: str) -> FrozenSet[str]:
        if resource == "*":
            return self._sections
        parts = resource.split("/")
        if len(parts) < 2 or parts[0] != "corridor" or parts[1] != self._corridor:
            return frozenset()
        if len(parts) == 2 or (len(parts) == 3 and parts[2] == "*"):
            return self._sections
        if len(parts) == 3 and parts[2] in self._sections:
            return frozenset({parts[2]})
        return frozenset()

    def _map(self, assignments: Any) -> Tuple[_Grant, ...]:
        merged: Dict[ActorRole, Tuple[Set[str], Set[str]]] = {}
        if not isinstance(assignments, list):
            return ()
        for item in assignments:
            if not isinstance(item, dict):
                continue
            role = self._role_map.get(item.get("role")) if isinstance(item.get("role"), str) else None
            resource = item.get("resource")
            perms = item.get("permissions")
            if role is None or not isinstance(resource, str) or not isinstance(perms, list):
                continue
            sections = self._sections_for(resource)
            if role is not ActorRole.ADMIN and not sections:
                continue  # a role with no usable scope grants nothing
            bucket = merged.setdefault(role, (set(), set()))
            bucket[0].update(sections if role is not ActorRole.ADMIN else ())
            bucket[1].update(p for p in perms if isinstance(p, str))
        return tuple(
            _Grant(role, frozenset(sections), frozenset(perms))
            for role, (sections, perms) in sorted(merged.items(), key=lambda kv: kv[0].value)
        )

    def _rebuild(self) -> None:
        people: List[Person] = []
        roles: List[RoleAssignment] = []
        scopes: List[RoleScopeAssignment] = []
        permissions: Dict[Tuple[str, ActorRole], FrozenSet[str]] = {}
        for person, grants in self._grants.values():
            people.append(person)
            for grant in grants:
                roles.append(RoleAssignment(person.person_id, grant.role))
                permissions[(person.person_id, grant.role)] = grant.permissions
                if grant.role is not ActorRole.ADMIN:
                    scopes.append(
                        RoleScopeAssignment(
                            person.person_id,
                            grant.role,
                            RailwayScope(self._corridor, grant.sections),
                        )
                    )
        # Built fully before it is published: a failure leaves the old snapshot in place.
        snapshot = IdentityDirectory(people, roles, scopes)
        self._snapshot = snapshot
        self._permissions = permissions


__all__ = ["NovaForgeDirectory", "permission_key", "person_id_for"]
