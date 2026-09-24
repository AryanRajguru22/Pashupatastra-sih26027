"""File-backed identity directory loader (Slice 10.2d.2).

    JSON file --(one read, strict parse)--> IdentityDirectory

The loader turns an operator-managed file into exactly one immutable
IdentityDirectory, built by the existing 10.2b constructor, or raises
DirectoryFileError. It is NOT authentication: it cannot verify an
assertion, build an Actor, assign assurance or name the capability. A file
can say which roles and scopes an already-verified identity maps to; it
can never say who someone is.

THE FILE IS UNTRUSTED INPUT
    The operator chooses the path, but the contents are treated as
    potentially malformed or hostile and fully validated.

    STRUCTURE is checked here (bytes, UTF-8, strict JSON, schema marker,
    version, exact key sets, JSON types). MEANING is not: duplicate
    identities, unknown persons, roles not held, ADMIN scope, id syntax,
    wildcard or empty scopes and duplicate assignments are refused by the
    existing constructors, and their reason codes pass through unchanged.

THE FORMAT (exactly three flat arrays, field names as in the dataclasses)

    {"schema": "pashupatastra.identity-directory", "version": 1,
     "people": [{"person_id": ..., "identity":
                    {"identity_provider": ..., "subject": ...}}],
     "role_assignments": [{"person_id": ..., "role": ...}],
     "scope_assignments": [{"person_id": ..., "role": ...,
                            "corridor_id": ..., "section_ids": [...]}]}

    No comments, no BOM, no trailing commas, no duplicate members, no
    unknown members, no missing members and no defaults: a missing array
    is never read as an empty one. An explicit document with three empty
    arrays is a valid, empty directory that grants nothing.

FAIL CLOSED, NEVER PARTIAL
    Any fault refuses the whole file. There is no partial directory, no
    skipped entry, no empty-directory fallback and no demo fallback.

NO VALUES IN ERRORS
    Errors carry a reason code and a structural location such as
    "people[3].identity". They never carry a provider, subject, person id,
    section id or any other value from the file, and the constructor's own
    exception is neither chained nor kept as context (its message echoes
    values).

READ ONCE, FROM ONE HANDLE
    The path is opened once, fstat is taken on that same handle, and at
    most MAX_DIRECTORY_FILE_BYTES + 1 bytes are read from it. The snapshot
    never consults the file again; changing the file afterwards has no
    effect. Nothing here writes, renames, reloads or reads the
    environment; the path is supplied by the caller.
"""

from __future__ import annotations

import os
import stat
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from backend.app.identity._jose import MalformedEncodingError, strict_json_object
from backend.app.identity.assignments import RoleAssignment
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment

SCHEMA_MARKER = "pashupatastra.identity-directory"
SUPPORTED_VERSION = 1
MAX_DIRECTORY_FILE_BYTES = 1024 * 1024

DIRECTORY_PATH_NOT_ABSOLUTE = "DIRECTORY_PATH_NOT_ABSOLUTE"
DIRECTORY_FILE_UNREADABLE = "DIRECTORY_FILE_UNREADABLE"
DIRECTORY_FILE_EMPTY = "DIRECTORY_FILE_EMPTY"
DIRECTORY_FILE_TOO_LARGE = "DIRECTORY_FILE_TOO_LARGE"
DIRECTORY_FILE_MALFORMED = "DIRECTORY_FILE_MALFORMED"
UNSUPPORTED_DIRECTORY_SCHEMA = "UNSUPPORTED_DIRECTORY_SCHEMA"

_UTF8_BOM = b"\xef\xbb\xbf"

_TOP_KEYS: FrozenSet[str] = frozenset(
    {"schema", "version", "people", "role_assignments", "scope_assignments"}
)
_PERSON_KEYS: FrozenSet[str] = frozenset({"person_id", "identity"})
_IDENTITY_KEYS: FrozenSet[str] = frozenset({"identity_provider", "subject"})
_ROLE_KEYS: FrozenSet[str] = frozenset({"person_id", "role"})
_SCOPE_KEYS: FrozenSet[str] = frozenset(
    {"person_id", "role", "corridor_id", "section_ids"}
)


class DirectoryFileError(ValueError):
    """The directory file was refused; see .reason and .location.

    The message names a rule and a structural location only, never a value
    read from the file.
    """

    def __init__(self, reason: str, location: str) -> None:
        super().__init__(f"{reason}: {location}")
        self.reason = reason
        self.location = location


def _malformed(location: str) -> DirectoryFileError:
    return DirectoryFileError(DIRECTORY_FILE_MALFORMED, location)


def _object(value: object, keys: FrozenSet[str], location: str) -> Dict[str, Any]:
    """value is a JSON object with exactly these members, or raise."""

    if type(value) is not dict or set(value) != keys:
        raise _malformed(location)
    return value  # type: ignore[return-value]


def _array(value: object, location: str) -> List[Any]:
    if type(value) is not list:
        raise _malformed(location)
    return value  # type: ignore[return-value]


def _string(value: object, location: str) -> str:
    if type(value) is not str:
        raise _malformed(location)
    return value  # type: ignore[return-value]


def _construct(location: str, build: Callable[[], Any]) -> Any:
    """Run an existing constructor; on refusal raise a value-free error.

    The reason comes from the constructor when it has one. The raise
    happens outside the except block so the constructor's exception (whose
    message echoes values) is neither the cause nor the context.
    """

    reason: Optional[str] = None
    result: Any = None

    try:
        result = build()
    except ValueError as exc:
        found = getattr(exc, "reason", None)
        reason = found if isinstance(found, str) and found else DIRECTORY_FILE_MALFORMED

    if reason is not None:
        raise DirectoryFileError(reason, location)

    return result


def parse_identity_directory(raw: bytes) -> IdentityDirectory:
    """Parse the file's bytes into an IdentityDirectory, or raise."""

    if type(raw) is not bytes:
        raise _malformed("document")

    if len(raw) > MAX_DIRECTORY_FILE_BYTES:
        raise DirectoryFileError(DIRECTORY_FILE_TOO_LARGE, "document")

    if not raw:
        raise DirectoryFileError(DIRECTORY_FILE_EMPTY, "document")

    if raw.startswith(_UTF8_BOM):
        raise _malformed("document")

    document: Optional[Dict[str, Any]] = None

    try:
        document = strict_json_object(raw)
    except MalformedEncodingError:
        pass

    if document is None:
        raise _malformed("document")

    # The header is judged first: a document from another schema or a
    # future version may legitimately have other members, and must be
    # reported as unsupported rather than as merely malformed.
    if document.get("schema") != SCHEMA_MARKER or type(document.get("schema")) is not str:
        raise DirectoryFileError(UNSUPPORTED_DIRECTORY_SCHEMA, "schema")

    version = document.get("version")
    if type(version) is not int or version != SUPPORTED_VERSION:
        raise DirectoryFileError(UNSUPPORTED_DIRECTORY_SCHEMA, "version")

    document = _object(document, _TOP_KEYS, "document")

    people: List[Person] = []
    for index, entry in enumerate(_array(document["people"], "people")):
        where = f"people[{index}]"
        item = _object(entry, _PERSON_KEYS, where)
        ident = _object(item["identity"], _IDENTITY_KEYS, f"{where}.identity")
        provider = _string(ident["identity_provider"], f"{where}.identity")
        subject = _string(ident["subject"], f"{where}.identity")
        person_id = _string(item["person_id"], where)

        people.append(
            _construct(
                where,
                lambda p=person_id, a=provider, s=subject: Person(
                    p, ExternalIdentity(a, s)
                ),
            )
        )

    roles: List[RoleAssignment] = []
    for index, entry in enumerate(
        _array(document["role_assignments"], "role_assignments")
    ):
        where = f"role_assignments[{index}]"
        item = _object(entry, _ROLE_KEYS, where)
        person_id = _string(item["person_id"], where)
        role = _string(item["role"], where)

        roles.append(
            _construct(where, lambda p=person_id, r=role: RoleAssignment(p, r))
        )

    scopes: List[RoleScopeAssignment] = []
    for index, entry in enumerate(
        _array(document["scope_assignments"], "scope_assignments")
    ):
        where = f"scope_assignments[{index}]"
        item = _object(entry, _SCOPE_KEYS, where)
        person_id = _string(item["person_id"], where)
        role = _string(item["role"], where)
        corridor_id = _string(item["corridor_id"], where)
        sections = _array(item["section_ids"], f"{where}.section_ids")

        for position, section in enumerate(sections):
            _string(section, f"{where}.section_ids[{position}]")

        scope = _construct(
            f"{where}.scope",
            lambda c=corridor_id, s=list(sections): RailwayScope(c, s),
        )
        scopes.append(
            _construct(
                where,
                lambda p=person_id, r=role, sc=scope: RoleScopeAssignment(p, r, sc),
            )
        )

    directory = _construct(
        "directory", lambda: IdentityDirectory(people, roles, scopes)
    )

    if type(directory) is not IdentityDirectory:
        raise _malformed("directory")

    return directory


def _read_regular_file(path: object) -> bytes:
    """At most cap + 1 bytes from ONE handle on a regular file, or raise."""

    if isinstance(path, os.PathLike):
        path = os.fspath(path)

    if not isinstance(path, str) or not path:
        raise DirectoryFileError(DIRECTORY_FILE_UNREADABLE, "path")

    if not os.path.isabs(path):
        raise DirectoryFileError(DIRECTORY_PATH_NOT_ABSOLUTE, "path")

    # O_NONBLOCK so opening a FIFO with no writer cannot hang startup; the
    # fstat below then refuses it. Read-only, and never creates a file.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    failure: Optional[str] = None
    raw = b""
    descriptor: Optional[int] = None

    try:
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)

            if not stat.S_ISREG(info.st_mode):
                failure = DIRECTORY_FILE_UNREADABLE
            elif info.st_size > MAX_DIRECTORY_FILE_BYTES:
                failure = DIRECTORY_FILE_TOO_LARGE
            else:
                # fdopen owns the descriptor from here and closes it.
                handle, descriptor = os.fdopen(descriptor, "rb"), None
                with handle:
                    raw = handle.read(MAX_DIRECTORY_FILE_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    except OSError:
        failure = DIRECTORY_FILE_UNREADABLE

    if failure is not None:
        raise DirectoryFileError(failure, "path")

    return raw


def load_identity_directory(path: "str | os.PathLike[str]") -> IdentityDirectory:
    """The IdentityDirectory the file at this absolute path describes, or raise.

    Reads the file once. The returned snapshot is independent of the file.
    """

    return parse_identity_directory(_read_regular_file(path))


__all__ = [
    "DIRECTORY_FILE_EMPTY",
    "DIRECTORY_FILE_MALFORMED",
    "DIRECTORY_FILE_TOO_LARGE",
    "DIRECTORY_FILE_UNREADABLE",
    "DIRECTORY_PATH_NOT_ABSOLUTE",
    "DirectoryFileError",
    "MAX_DIRECTORY_FILE_BYTES",
    "SCHEMA_MARKER",
    "SUPPORTED_VERSION",
    "UNSUPPORTED_DIRECTORY_SCHEMA",
    "load_identity_directory",
    "parse_identity_directory",
]
