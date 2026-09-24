"""Slice 10.2d.2: the file-backed IdentityDirectory loader.

Hygiene: every file lives under tmp_path, every service is built on a tmp
database, neither `main` nor `router` is re-imported, and the repository's
own jobs.db is hashed before and after the module.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import random
import stat
import traceback
import types
import uuid
from pathlib import Path

import pytest

from backend.app.api.composition import (
    AUTH_CONFIG_IN_DEMO_MODE,
    AUTHENTICATION_MECHANISM_UNAVAILABLE,
    DIRECTORY_IN_DEMO_MODE,
    AuthConfigurationError,
    AuthMode,
    compose_http_jobs,
    compose_jobs,
    resolve_auth_mode,
)
from backend.app.identity import directory_file as loader_module
from backend.app.identity.actor import (
    Actor,
    ActorRole,
    IdentityAssurance,
    human_actor,
)
from backend.app.identity.assignments import (
    DUPLICATE_ASSIGNMENT,
    INVALID_PERSON_ID,
    INVALID_ROLE,
    ROLE_SELECTION_REQUIRED,
    AssignmentError,
    RoleAssignment,
)
from backend.app.identity.authenticated_actor import authenticated_actor
from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.directory import (
    ADMIN_HAS_NO_SCOPE,
    DUPLICATE_EXTERNAL_IDENTITY,
    DUPLICATE_PERSON,
    DUPLICATE_PERSON_ID,
    IDENTITY_NOT_ENROLLED,
    ROLE_NOT_HELD,
    UNKNOWN_PERSON,
    DirectoryError,
    IdentityDirectory,
)
from backend.app.identity.directory_file import (
    DIRECTORY_FILE_EMPTY,
    DIRECTORY_FILE_MALFORMED,
    DIRECTORY_FILE_TOO_LARGE,
    DIRECTORY_FILE_UNREADABLE,
    DIRECTORY_PATH_NOT_ABSOLUTE,
    MAX_DIRECTORY_FILE_BYTES,
    SCHEMA_MARKER,
    UNSUPPORTED_DIRECTORY_SCHEMA,
    DirectoryFileError,
    load_identity_directory,
    parse_identity_directory,
)
from backend.app.identity.identity_assertion import (
    IdentityAssertionVerifier,
    InMemoryReplayGuard,
    VerifiedIdentityAssertion,
)
from backend.app.identity.person import ExternalIdentity, Person, PersonError
from backend.app.identity.resource import ResourceLocation
from backend.app.identity.scope import (
    DUPLICATE_SECTION,
    EMPTY_SCOPE,
    INVALID_CORRIDOR,
    INVALID_SECTION,
    WILDCARD_SECTION,
    RailwayScope,
)
from backend.app.identity.scope_assignment import RoleScopeAssignment
from backend.app.identity.trusted_keys import StaticKeyProvider
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.tests.test_slice10_2a_identity_assertion import (
    ALIAS,
    FixedClock,
    SigningKey,
    base_claims,
    config,
    jwks,
    mint,
)

W, E, A, ADM = (
    ActorRole.WORKER,
    ActorRole.ENGINEER,
    ActorRole.AUTHORITY,
    ActorRole.ADMIN,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REPO_JOBS_DB = Path(__file__).resolve().parents[2] / "jobs.db"
LOADER_SOURCE = Path(loader_module.__file__).read_text(encoding="utf-8")


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True, scope="module")
def _repository_jobs_db_is_never_touched():
    before = _sha256(REPO_JOBS_DB)
    yield
    assert _sha256(REPO_JOBS_DB) == before


# ---------------------------------------------------------------- helpers


def _person(pid: str, subject: str, provider: str = "novaforge") -> dict:
    return {
        "person_id": pid,
        "identity": {"identity_provider": provider, "subject": subject},
    }


def good_doc() -> dict:
    return {
        "schema": SCHEMA_MARKER,
        "version": 1,
        "people": [
            _person("P-1", "sub-1"),
            _person("P-2", "sub-2"),
            _person("P-ADMIN", "sub-admin"),
        ],
        "role_assignments": [
            {"person_id": "P-1", "role": "WORKER"},
            {"person_id": "P-1", "role": "ENGINEER"},
            {"person_id": "P-2", "role": "AUTHORITY"},
            {"person_id": "P-ADMIN", "role": "ADMIN"},
        ],
        "scope_assignments": [
            {"person_id": "P-1", "role": "WORKER", "corridor_id": "C1",
             "section_ids": ["S1", "S2"]},
            {"person_id": "P-1", "role": "WORKER", "corridor_id": "C2",
             "section_ids": ["S9"]},
            {"person_id": "P-1", "role": "ENGINEER", "corridor_id": "C1",
             "section_ids": ["S3"]},
            {"person_id": "P-2", "role": "AUTHORITY", "corridor_id": "C1",
             "section_ids": ["S1"]},
        ],
    }


def encode(doc: object) -> bytes:
    return json.dumps(doc).encode("utf-8")


def mutated(fn) -> dict:
    doc = good_doc()
    fn(doc)
    return doc


def code_directory() -> IdentityDirectory:
    """The same dataset as good_doc(), built directly from the constructors."""

    def ident(subject: str) -> ExternalIdentity:
        return ExternalIdentity("novaforge", subject)

    return IdentityDirectory(
        people=[
            Person("P-1", ident("sub-1")),
            Person("P-2", ident("sub-2")),
            Person("P-ADMIN", ident("sub-admin")),
        ],
        role_assignments=[
            RoleAssignment("P-1", W),
            RoleAssignment("P-1", E),
            RoleAssignment("P-2", A),
            RoleAssignment("P-ADMIN", ADM),
        ],
        scope_assignments=[
            RoleScopeAssignment("P-1", W, RailwayScope("C1", ["S1", "S2"])),
            RoleScopeAssignment("P-1", W, RailwayScope("C2", ["S9"])),
            RoleScopeAssignment("P-1", E, RailwayScope("C1", ["S3"])),
            RoleScopeAssignment("P-2", A, RailwayScope("C1", ["S1"])),
        ],
    )


def build_directly(doc: dict) -> IdentityDirectory:
    """The constructors' own answer for a document, with no loader in between."""

    return IdentityDirectory(
        [Person(p["person_id"], ExternalIdentity(**p["identity"])) for p in doc["people"]],
        [RoleAssignment(r["person_id"], r["role"]) for r in doc["role_assignments"]],
        [
            RoleScopeAssignment(
                s["person_id"], s["role"], RailwayScope(s["corridor_id"], s["section_ids"])
            )
            for s in doc["scope_assignments"]
        ],
    )


def snapshot(d: IdentityDirectory):
    return (d.people, d.role_assignments, d.scope_assignments)


def write(tmp_path: Path, data: object, name: str = "identity-directory.json") -> Path:
    path = tmp_path / name
    path.write_bytes(data if isinstance(data, bytes) else encode(data))
    return path


def refused(data: object, reason: str) -> DirectoryFileError:
    raw = data if isinstance(data, bytes) else encode(data)

    with pytest.raises(DirectoryFileError) as info:
        parse_identity_directory(raw)

    assert info.value.reason == reason
    assert info.value.__cause__ is None
    assert info.value.__context__ is None
    return info.value


# --------------------------------------------- 1. round trip and equivalence


def test_valid_file_round_trips_to_an_exact_identity_directory(tmp_path):
    loaded = load_identity_directory(write(tmp_path, good_doc()))

    assert type(loaded) is IdentityDirectory
    assert snapshot(loaded) == snapshot(code_directory())


def test_parse_and_load_agree_and_accept_str_and_pathlike(tmp_path):
    path = write(tmp_path, good_doc())
    parsed = parse_identity_directory(encode(good_doc()))

    assert snapshot(load_identity_directory(path)) == snapshot(parsed)
    assert snapshot(load_identity_directory(str(path))) == snapshot(parsed)


def test_loaded_directory_behaves_like_the_code_built_one():
    loaded = parse_identity_directory(encode(good_doc()))
    built = code_directory()
    identity = ExternalIdentity("novaforge", "sub-1")

    assert loaded.person_for(identity) == built.person_for(identity)
    assert loaded.select_role(loaded.person("P-2")) == built.select_role(built.person("P-2"))

    for actor_id in ("P-1", "P-2", "P-ADMIN", "P-404"):
        for role in (W, E, A, ADM):
            assert loaded.scopes_for_actor(actor_id, role) == built.scopes_for_actor(actor_id, role)


def test_scopes_stay_per_role_after_loading():
    loaded = parse_identity_directory(encode(good_doc()))

    assert loaded.scopes_for_actor("P-1", W) == (
        RailwayScope("C1", ["S1", "S2"]),
        RailwayScope("C2", ["S9"]),
    )
    assert loaded.scopes_for_actor("P-1", E) == (RailwayScope("C1", ["S3"]),)
    assert loaded.scopes_for_actor("P-1", A) == ()
    assert loaded.scopes_for_actor("P-ADMIN", ADM) == ()


# ---------------------------------------------------- 2. order independence


@pytest.mark.parametrize("seed", range(8))
def test_array_and_section_order_never_changes_the_snapshot(seed):
    rng = random.Random(seed)
    doc = good_doc()

    for key in ("people", "role_assignments", "scope_assignments"):
        rng.shuffle(doc[key])
    for entry in doc["scope_assignments"]:
        rng.shuffle(entry["section_ids"])

    assert snapshot(parse_identity_directory(encode(doc))) == snapshot(code_directory())


def test_json_member_order_is_irrelevant():
    doc = good_doc()
    reordered = {k: doc[k] for k in reversed(list(doc))}

    assert snapshot(parse_identity_directory(encode(reordered))) == snapshot(code_directory())


# ------------------------------ 3. immutable snapshot, independent of the file


def test_snapshot_is_independent_of_the_file_after_loading(tmp_path):
    path = write(tmp_path, good_doc())
    loaded = load_identity_directory(path)
    expected = snapshot(code_directory())

    path.write_bytes(encode({**good_doc(), "people": [], "role_assignments": [], "scope_assignments": []}))
    assert snapshot(loaded) == expected

    path.write_bytes(b"not json at all")
    assert snapshot(loaded) == expected

    path.unlink()
    assert snapshot(loaded) == expected
    assert loaded.scopes_for_actor("P-1", W) == code_directory().scopes_for_actor("P-1", W)


def test_loaded_directory_is_immutable():
    loaded = parse_identity_directory(encode(good_doc()))

    with pytest.raises(AttributeError):
        loaded.people = ()
    with pytest.raises(AttributeError):
        del loaded.people
    with pytest.raises(AttributeError):
        loaded.brand_new = 1


def test_loading_does_not_modify_or_create_anything(tmp_path):
    path = write(tmp_path, good_doc())
    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(tmp_path)))

    load_identity_directory(path)

    assert (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(tmp_path))) == before


def test_a_failed_load_creates_nothing(tmp_path):
    with pytest.raises(DirectoryFileError):
        load_identity_directory(tmp_path / "missing.json")

    assert os.listdir(tmp_path) == []


# ------------------------------------------------- 4. explicit empty dataset


def empty_doc() -> dict:
    return {**good_doc(), "people": [], "role_assignments": [], "scope_assignments": []}


def test_explicit_empty_document_is_a_valid_directory_that_grants_nothing():
    loaded = parse_identity_directory(encode(empty_doc()))

    assert type(loaded) is IdentityDirectory
    assert loaded.people == () and loaded.role_assignments == () and loaded.scope_assignments == ()

    with pytest.raises(DirectoryError) as info:
        loaded.person_for(ExternalIdentity("novaforge", "sub-1"))
    assert info.value.reason == IDENTITY_NOT_ENROLLED

    for role in (W, E, A, ADM):
        assert loaded.scopes_for_actor("P-1", role) == ()


@pytest.mark.parametrize("array", ["people", "role_assignments", "scope_assignments"])
def test_a_missing_array_is_refused_never_read_as_empty(array):
    doc = empty_doc()
    del doc[array]

    refused(doc, DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize("array", ["people", "role_assignments", "scope_assignments"])
def test_a_null_array_is_refused_never_read_as_empty(array):
    refused({**empty_doc(), array: None}, DIRECTORY_FILE_MALFORMED)


# ---------------------------------------------------- 5. file-level failures


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(tmp_path / "absent.json")

    assert info.value.reason == DIRECTORY_FILE_UNREADABLE


@pytest.mark.parametrize("path", ["identity-directory.json", "./x.json", "..", "."])
def test_relative_path_is_refused(path):
    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(path)

    assert info.value.reason == DIRECTORY_PATH_NOT_ABSOLUTE


@pytest.mark.parametrize("path", [None, 5, b"/x", "", ["/x"]])
def test_non_path_arguments_are_refused(path):
    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(path)

    assert info.value.reason == DIRECTORY_FILE_UNREADABLE


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(tmp_path)

    assert info.value.reason == DIRECTORY_FILE_UNREADABLE


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_real_fifo_is_refused_and_cannot_hang_startup(tmp_path):
    fifo = tmp_path / "directory.fifo"
    os.mkfifo(fifo)

    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(fifo)

    assert info.value.reason == DIRECTORY_FILE_UNREADABLE


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFDIR, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK])
def test_only_a_regular_file_is_read(tmp_path, monkeypatch, mode):
    path = write(tmp_path, good_doc())
    real_fstat = os.fstat

    def fake_fstat(fd):
        real_fstat(fd)
        return types.SimpleNamespace(st_mode=mode | 0o600, st_size=10)

    with monkeypatch.context() as m:
        m.setattr(loader_module.os, "fstat", fake_fstat)
        with pytest.raises(DirectoryFileError) as info:
            load_identity_directory(path)

    assert info.value.reason == DIRECTORY_FILE_UNREADABLE


def test_the_file_is_opened_once_and_fstat_uses_that_handle(tmp_path, monkeypatch):
    path = write(tmp_path, good_doc())
    real_open, real_fstat = os.open, os.fstat
    opened: list = []
    statted: list = []

    def counting_open(target, *args, **kwargs):
        fd = real_open(target, *args, **kwargs)
        if os.fspath(target) == str(path):
            opened.append(fd)
        return fd

    def recording_fstat(fd):
        statted.append(fd)
        return real_fstat(fd)

    with monkeypatch.context() as m:
        m.setattr(loader_module.os, "open", counting_open)
        m.setattr(loader_module.os, "fstat", recording_fstat)
        load_identity_directory(path)

    assert len(opened) == 1
    assert statted == opened


def test_the_file_is_never_stat_ed_by_path(tmp_path, monkeypatch):
    path = write(tmp_path, good_doc())

    def forbidden(*args, **kwargs):
        raise AssertionError("path-based stat would allow a TOCTOU re-open")

    with monkeypatch.context() as m:
        for name in ("stat", "lstat"):
            m.setattr(loader_module.os, name, forbidden)
        load_identity_directory(path)


def test_zero_bytes_is_empty_never_an_empty_directory(tmp_path):
    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(write(tmp_path, b""))
    assert info.value.reason == DIRECTORY_FILE_EMPTY

    refused(b"", DIRECTORY_FILE_EMPTY)


def test_a_file_of_exactly_the_cap_loads(tmp_path):
    raw = encode(good_doc())
    raw += b" " * (MAX_DIRECTORY_FILE_BYTES - len(raw))
    assert len(raw) == MAX_DIRECTORY_FILE_BYTES

    assert snapshot(load_identity_directory(write(tmp_path, raw))) == snapshot(code_directory())


def test_one_byte_over_the_cap_is_refused(tmp_path):
    raw = encode(good_doc())
    raw += b" " * (MAX_DIRECTORY_FILE_BYTES + 1 - len(raw))

    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(write(tmp_path, raw))
    assert info.value.reason == DIRECTORY_FILE_TOO_LARGE

    refused(raw, DIRECTORY_FILE_TOO_LARGE)


def test_a_file_that_grows_past_a_small_reported_size_is_still_capped(tmp_path, monkeypatch):
    raw = encode(good_doc()) + b" " * (MAX_DIRECTORY_FILE_BYTES + 10)
    path = write(tmp_path, raw)
    real_fstat = os.fstat

    def lying_fstat(fd):
        info = real_fstat(fd)
        return types.SimpleNamespace(st_mode=info.st_mode, st_size=10)

    with monkeypatch.context() as m:
        m.setattr(loader_module.os, "fstat", lying_fstat)
        with pytest.raises(DirectoryFileError) as info:
            load_identity_directory(path)

    assert info.value.reason == DIRECTORY_FILE_TOO_LARGE


@pytest.mark.parametrize(
    "raw",
    [b"", b"x" * (MAX_DIRECTORY_FILE_BYTES + 1), b"\xef\xbb\xbf{}"],
    ids=["empty", "oversized", "bom"],
)
def test_size_emptiness_and_bom_are_judged_before_any_json_parsing(raw, monkeypatch):
    def forbidden(_):
        raise AssertionError("parsed before the size/empty/BOM check")

    with monkeypatch.context() as m:
        m.setattr(loader_module, "strict_json_object", forbidden)
        with pytest.raises(DirectoryFileError):
            parse_identity_directory(raw)


def test_a_utf8_bom_is_refused():
    refused(b"\xef\xbb\xbf" + encode(good_doc()), DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe{}",
        b'{"schema": "\xc3\x28"}',
        json.dumps(good_doc()).encode("utf-16"),
        json.dumps(good_doc()).encode("utf-32"),
        b"\x00",
    ],
)
def test_non_utf8_bytes_are_refused(raw):
    refused(raw, DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize(
    "raw",
    [
        b"   \n\t ",
        b"not json",
        b"{",
        b"}",
        b"{'schema': 'x'}",
        b"{schema: 1}",
        b'{"a": 1,}',
        b'{"a": 1} // comment',
        b'// leading\n{"a": 1}',
        b'/* c */ {"a": 1}',
        b'{"a": 1} {"b": 2}',
        b'{"a": 1} garbage',
        b"[" * 100_000,
        b"\x00" + encode(good_doc()),
    ],
    ids=lambda raw: f"{len(raw)}-bytes-{raw[:12]!r}",
)
def test_invalid_json_is_refused(raw):
    refused(raw, DIRECTORY_FILE_MALFORMED)


def test_a_truncated_partial_write_is_refused():
    raw = encode(good_doc())

    for cut in (1, 10, len(raw) // 2, len(raw) - 1):
        refused(raw[:cut], DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize("raw", [b"[]", b'"x"', b"5", b"null", b"true", b"[{}]"])
def test_top_level_must_be_an_object(raw):
    refused(raw, DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize(
    "text",
    [
        '{"schema": "%s", "schema": "%s", "version": 1}' % (SCHEMA_MARKER, SCHEMA_MARKER),
        '{"schema": "%s", "version": 1, "people": [], "people": [],'
        ' "role_assignments": [], "scope_assignments": []}' % SCHEMA_MARKER,
        '{"schema": "%s", "version": 1, "people": [{"person_id": "A", "person_id": "B",'
        ' "identity": {"identity_provider": "n", "subject": "s"}}],'
        ' "role_assignments": [], "scope_assignments": []}' % SCHEMA_MARKER,
        '{"schema": "%s", "version": 1, "people": [{"person_id": "A", "identity":'
        ' {"subject": "s", "subject": "t", "identity_provider": "n"}}],'
        ' "role_assignments": [], "scope_assignments": []}' % SCHEMA_MARKER,
    ],
)
def test_duplicate_json_members_are_refused_at_every_level(text):
    refused(text.encode(), DIRECTORY_FILE_MALFORMED)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_refused(constant):
    text = encode(good_doc()).decode().replace('"version": 1', f'"version": {constant}')
    assert constant in text

    refused(text.encode(), DIRECTORY_FILE_MALFORMED)


def test_non_bytes_input_to_parse_is_refused():
    for bad in ("{}", bytearray(b"{}"), None, 5, memoryview(b"{}")):
        with pytest.raises(DirectoryFileError) as info:
            parse_identity_directory(bad)
        assert info.value.reason == DIRECTORY_FILE_MALFORMED


# ------------------------------------------------------- 6. schema / version


@pytest.mark.parametrize(
    "schema",
    ["", "pashupatastra.identity-directory ", "Pashupatastra.identity-directory",
     "pashupatastra.identity-directory.v2", "novaforge", None, 5, True, [SCHEMA_MARKER]],
)
def test_wrong_schema_marker_is_unsupported(schema):
    refused({**good_doc(), "schema": schema}, UNSUPPORTED_DIRECTORY_SCHEMA)


@pytest.mark.parametrize("version", [0, 2, -1, 10**30, "1", True, False, 1.0, None, [1], {"v": 1}])
def test_only_the_integer_one_is_a_supported_version(version):
    refused({**good_doc(), "version": version}, UNSUPPORTED_DIRECTORY_SCHEMA)


@pytest.mark.parametrize("member", ["schema", "version"])
def test_missing_header_member_is_unsupported(member):
    doc = good_doc()
    del doc[member]

    refused(doc, UNSUPPORTED_DIRECTORY_SCHEMA)


def test_a_future_version_with_other_members_is_unsupported_not_partially_read():
    doc = {"schema": SCHEMA_MARKER, "version": 2, "entries": [], "people": "new-shape"}

    refused(doc, UNSUPPORTED_DIRECTORY_SCHEMA)


def test_an_empty_object_is_unsupported():
    refused(b"{}", UNSUPPORTED_DIRECTORY_SCHEMA)


# ------------------------------- 7. unknown / missing keys, wrong JSON types

PATHS = {
    "top": (),
    "person": ("people", 0),
    "identity": ("people", 0, "identity"),
    "role": ("role_assignments", 0),
    "scope": ("scope_assignments", 0),
}


def _at(doc: dict, path: tuple):
    node = doc
    for step in path:
        node = node[step]
    return node


@pytest.mark.parametrize("where", list(PATHS))
@pytest.mark.parametrize(
    "extra", ["assurance", "actor", "role_authenticated", "capability", "admin", "scope", "x"]
)
def test_unknown_members_are_refused_at_every_level(where, extra):
    doc = good_doc()
    _at(doc, PATHS[where])[extra] = "AUTHENTICATED"

    refused(doc, DIRECTORY_FILE_MALFORMED)


def _member_cases():
    for where, path in PATHS.items():
        for key in _at(good_doc(), path):
            yield where, key


@pytest.mark.parametrize("where,key", list(_member_cases()))
def test_missing_members_are_refused_at_every_level(where, key):
    doc = good_doc()
    del _at(doc, PATHS[where])[key]

    reason = (
        UNSUPPORTED_DIRECTORY_SCHEMA
        if where == "top" and key in ("schema", "version")
        else DIRECTORY_FILE_MALFORMED
    )
    refused(doc, reason)


WRONG_TYPES = [
    (("people",), {}), (("people",), "x"), (("people",), 5),
    (("role_assignments",), {}), (("role_assignments",), "x"),
    (("scope_assignments",), {}), (("scope_assignments",), "x"),
    (("people", 0), "P-1"), (("people", 0), None), (("people", 0), []),
    (("role_assignments", 0), "P-1"), (("role_assignments", 0), None),
    (("scope_assignments", 0), []), (("scope_assignments", 0), 5),
    (("people", 0, "person_id"), 5), (("people", 0, "person_id"), None),
    (("people", 0, "person_id"), ["P-1"]), (("people", 0, "person_id"), True),
    (("people", 0, "identity"), "novaforge"), (("people", 0, "identity"), []),
    (("people", 0, "identity"), None),
    (("people", 0, "identity", "identity_provider"), 5),
    (("people", 0, "identity", "identity_provider"), None),
    (("people", 0, "identity", "subject"), 5),
    (("people", 0, "identity", "subject"), None),
    (("people", 0, "identity", "subject"), ["sub-1"]),
    (("role_assignments", 0, "person_id"), 5),
    (("role_assignments", 0, "role"), 5), (("role_assignments", 0, "role"), None),
    (("role_assignments", 0, "role"), ["WORKER"]), (("role_assignments", 0, "role"), True),
    (("scope_assignments", 0, "person_id"), None),
    (("scope_assignments", 0, "role"), 1),
    (("scope_assignments", 0, "corridor_id"), 5),
    (("scope_assignments", 0, "corridor_id"), None),
    (("scope_assignments", 0, "section_ids"), "S1"),
    (("scope_assignments", 0, "section_ids"), {"S1": 1}),
    (("scope_assignments", 0, "section_ids"), None),
    (("scope_assignments", 0, "section_ids"), 5),
    (("scope_assignments", 0, "section_ids", 0), 5),
    (("scope_assignments", 0, "section_ids", 0), None),
    (("scope_assignments", 0, "section_ids", 0), ["S1"]),
    (("scope_assignments", 0, "section_ids", 0), True),
]


@pytest.mark.parametrize("path,value", WRONG_TYPES)
def test_wrong_json_types_are_refused(path, value):
    doc = good_doc()
    _at(doc, path[:-1])[path[-1]] = value

    refused(doc, DIRECTORY_FILE_MALFORMED)


# --------------------------------------------------- 8. parity with 10.2b


def _append(key, entry):
    return lambda doc: doc[key].append(entry)


_P3 = _person("P-3", "sub-3")
_SCOPE = {"person_id": "P-1", "role": "WORKER", "corridor_id": "C1", "section_ids": ["S1"]}


def _set_scope(**over):
    return lambda doc: doc["scope_assignments"].append({**_SCOPE, **over})


def _set_role(value):
    return lambda doc: doc["role_assignments"].append({"person_id": "P-3", "role": value})


# (case id, mutation, expected reason the CONSTRUCTORS themselves report)
PARITY = [
    ("duplicate-external-identity",
     lambda d: d["people"].append(_person("P-3", "sub-1")), DUPLICATE_EXTERNAL_IDENTITY),
    ("duplicate-person-id",
     lambda d: d["people"].append(_person("P-1", "sub-other")), DUPLICATE_PERSON_ID),
    ("duplicate-person-entry",
     lambda d: d["people"].append(copy.deepcopy(d["people"][0])), DUPLICATE_PERSON),
    ("role-for-unknown-person",
     _append("role_assignments", {"person_id": "P-404", "role": "WORKER"}), UNKNOWN_PERSON),
    ("scope-for-unknown-person", _set_scope(person_id="P-404"), UNKNOWN_PERSON),
    ("scope-for-role-not-held", _set_scope(person_id="P-2"), ROLE_NOT_HELD),
    ("scope-under-other-held-role", _set_scope(person_id="P-2", role="WORKER"), ROLE_NOT_HELD),
    ("scope-for-role-held-by-someone-else", _set_scope(role="AUTHORITY"), ROLE_NOT_HELD),
    ("admin-scope", _set_scope(person_id="P-ADMIN", role="ADMIN"), ADMIN_HAS_NO_SCOPE),
    ("admin-all-sections-scope",
     _set_scope(person_id="P-ADMIN", role="ADMIN", section_ids=["S1", "S2", "S3", "S9"]),
     ADMIN_HAS_NO_SCOPE),
    ("role-lowercase", _set_role("worker"), INVALID_ROLE),
    ("role-system", _set_role("SYSTEM"), INVALID_ROLE),
    ("role-unidentified", _set_role("UNIDENTIFIED"), INVALID_ROLE),
    ("role-unknown", _set_role("SUPER_ADMIN"), INVALID_ROLE),
    ("role-empty", _set_role(""), INVALID_ROLE),
    ("role-padded", _set_role(" WORKER"), INVALID_ROLE),
    ("scope-role-system", _set_scope(role="SYSTEM"), INVALID_ROLE),
    ("role-assignment-bad-person-id",
     _append("role_assignments", {"person_id": "SYSTEM-1", "role": "WORKER"}), INVALID_PERSON_ID),
    ("role-assignment-blank-person-id",
     _append("role_assignments", {"person_id": "", "role": "WORKER"}), INVALID_PERSON_ID),
    ("duplicate-role-assignment",
     lambda d: d["role_assignments"].append(copy.deepcopy(d["role_assignments"][0])),
     DUPLICATE_ASSIGNMENT),
    ("duplicate-scope-assignment-reordered",
     lambda d: d["scope_assignments"].append(
         {**d["scope_assignments"][0], "section_ids": ["S2", "S1"]}),
     DUPLICATE_ASSIGNMENT),
    ("wildcard-star", _set_scope(section_ids=["*"]), WILDCARD_SECTION),
    ("wildcard-embedded-star", _set_scope(section_ids=["S*"]), WILDCARD_SECTION),
    ("wildcard-ALL", _set_scope(section_ids=["ALL"]), WILDCARD_SECTION),
    ("wildcard-all-lowercase", _set_scope(section_ids=["all"]), WILDCARD_SECTION),
    ("empty-scope", _set_scope(section_ids=[]), EMPTY_SCOPE),
    ("duplicate-section", _set_scope(section_ids=["S1", "S1"]), DUPLICATE_SECTION),
    ("blank-section", _set_scope(section_ids=[""]), INVALID_SECTION),
    ("padded-section", _set_scope(section_ids=[" S1"]), INVALID_SECTION),
    ("control-char-section", _set_scope(section_ids=["S\x001"]), INVALID_SECTION),
    ("blank-corridor", _set_scope(corridor_id=""), INVALID_CORRIDOR),
    ("padded-corridor", _set_scope(corridor_id=" C1"), INVALID_CORRIDOR),
    ("wildcard-corridor", _set_scope(corridor_id="*"), INVALID_CORRIDOR),
]


@pytest.mark.parametrize("case,mutation,reason", PARITY, ids=[c[0] for c in PARITY])
def test_every_10_2b_refusal_surfaces_with_the_same_reason(case, mutation, reason):
    doc = mutated(mutation)

    # The constructors, given the same data directly, refuse with this reason...
    with pytest.raises(ValueError) as direct:
        build_directly(doc)
    assert direct.value.reason == reason

    # ...and the file loader passes that reason through unchanged.
    refused(doc, reason)


PERSON_LEVEL = [
    ("person-id-system-prefix", lambda d: d["people"].append(_person("SYSTEM-1", "s9"))),
    ("person-id-blank", lambda d: d["people"].append(_person("", "s9"))),
    ("person-id-space", lambda d: d["people"].append(_person("P 9", "s9"))),
    ("person-id-too-long", lambda d: d["people"].append(_person("P" * 65, "s9"))),
    ("provider-blank", lambda d: d["people"].append(_person("P-9", "s9", provider=""))),
    ("provider-padded", lambda d: d["people"].append(_person("P-9", "s9", provider=" novaforge"))),
    ("provider-too-long", lambda d: d["people"].append(_person("P-9", "s9", provider="p" * 65))),
    ("subject-blank", lambda d: d["people"].append(_person("P-9", "  "))),
    ("subject-padded", lambda d: d["people"].append(_person("P-9", "s9 "))),
    ("subject-control-char", lambda d: d["people"].append(_person("P-9", "s\x009"))),
    ("subject-too-long", lambda d: d["people"].append(_person("P-9", "s" * 257))),
    ("subject-lone-surrogate", lambda d: d["people"].append(_person("P-9", "s\ud800"))),
]


@pytest.mark.parametrize("case,mutation", PERSON_LEVEL, ids=[c[0] for c in PERSON_LEVEL])
def test_invalid_person_and_identity_values_are_refused_as_malformed(case, mutation):
    doc = mutated(mutation)

    with pytest.raises(PersonError):
        build_directly(doc)

    error = refused(doc, DIRECTORY_FILE_MALFORMED)
    assert error.location == f"people[{len(doc['people']) - 1}]"


def test_one_bad_entry_refuses_the_whole_file():
    doc = good_doc()
    doc["scope_assignments"].append({**_SCOPE, "section_ids": ["*"]})

    refused(doc, WILDCARD_SECTION)


def test_semantic_validation_is_not_duplicated_in_the_loader():
    tree = ast.parse(LOADER_SOURCE)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    semantic = {
        "ROLE_NOT_HELD", "ADMIN_HAS_NO_SCOPE", "DUPLICATE_EXTERNAL_IDENTITY",
        "DUPLICATE_PERSON", "DUPLICATE_PERSON_ID", "UNKNOWN_PERSON",
        "DUPLICATE_ASSIGNMENT", "INVALID_ROLE", "INVALID_PERSON_ID",
        "WILDCARD_SECTION", "EMPTY_SCOPE", "DUPLICATE_SECTION", "INVALID_SECTION",
        "INVALID_CORRIDOR", "HUMAN_ROLES", "ActorRole", "DirectoryError",
    }

    assert names & semantic == set()


# ------------- 9. no values in errors, no chained cause, no context

_SECRET = "SECRET-VALUE-9f3a"


def _leaky_documents():
    yield "unknown-key-name", mutated(lambda d: d["people"][0].update({_SECRET: 1}))
    yield "unknown-top-key", mutated(lambda d: d.update({_SECRET: 1}))
    yield "duplicate-identity", mutated(
        lambda d: d["people"].append(_person("P-3", "sub-1", provider="novaforge"))
    )
    yield "duplicate-identity-secret", mutated(
        lambda d: d["people"].extend(
            [_person("P-S1", _SECRET), _person("P-S2", _SECRET)]
        )
    )
    yield "unknown-person-id", mutated(
        lambda d: d["role_assignments"].append({"person_id": _SECRET, "role": "WORKER"})
    )
    yield "unknown-person-scope", mutated(_set_scope(person_id=_SECRET))
    yield "bad-role-value", mutated(_set_role(_SECRET))
    yield "invalid-subject", mutated(
        lambda d: d["people"].append(_person("P-9", " " + _SECRET))
    )
    yield "invalid-provider", mutated(
        lambda d: d["people"].append(_person("P-9", "s", provider=_SECRET + " "))
    )
    yield "invalid-person-id", mutated(
        lambda d: d["people"].append(_person(_SECRET + " x", "s9"))
    )
    yield "wildcard-section", mutated(_set_scope(section_ids=[_SECRET + "*"]))
    yield "duplicate-section", mutated(_set_scope(section_ids=[_SECRET, _SECRET]))
    yield "bad-corridor", mutated(_set_scope(corridor_id=" " + _SECRET))
    yield "role-not-held", mutated(_set_scope(person_id="P-2", role="WORKER", corridor_id=_SECRET))
    yield "wrong-type", mutated(lambda d: d["people"][0].update({"person_id": [_SECRET]}))
    yield "schema-value", {**good_doc(), "schema": _SECRET}
    yield "version-value", {**good_doc(), "version": _SECRET}


@pytest.mark.parametrize("name,doc", list(_leaky_documents()), ids=lambda v: v if isinstance(v, str) else "")
def test_errors_carry_no_file_values_and_no_chained_exception(name, doc):
    with pytest.raises(DirectoryFileError) as info:
        parse_identity_directory(encode(doc))

    error = info.value
    rendered = "".join(traceback.format_exception(error))
    haystacks = [str(error), repr(error), error.reason, error.location, rendered]

    for text in haystacks:
        assert _SECRET not in text
    for value in ("sub-1", "sub-2", "sub-admin", "P-ADMIN", "novaforge"):
        assert value not in str(error) and value not in error.location

    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False or error.__context__ is None


def test_error_message_is_only_reason_and_structural_location():
    error = refused(mutated(_set_scope(section_ids=["*"])), WILDCARD_SECTION)

    assert str(error) == f"{WILDCARD_SECTION}: scope_assignments[4].scope"
    assert error.location == "scope_assignments[4].scope"


def test_file_level_errors_do_not_echo_the_path(tmp_path):
    path = tmp_path / f"{_SECRET}.json"

    with pytest.raises(DirectoryFileError) as info:
        load_identity_directory(path)

    assert _SECRET not in str(info.value) and _SECRET not in info.value.location
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_directory_file_error_is_not_a_directory_error():
    assert not issubclass(DirectoryFileError, DirectoryError)
    assert issubclass(DirectoryFileError, ValueError)


# ------------------------ 10. authenticated end to end, through compose_jobs

SUB_ONE, SUB_NARROW, SUB_MULTI, SUB_ADMIN, SUB_STRANGER = (str(uuid.uuid4()) for _ in range(5))


@pytest.fixture()
def key() -> SigningKey:
    return SigningKey("key-current")


@pytest.fixture()
def verifier(key) -> IdentityAssertionVerifier:
    return IdentityAssertionVerifier(
        config(), StaticKeyProvider(jwks(key)), InMemoryReplayGuard(), FixedClock()
    )


def verified(key, verifier, subject: str) -> VerifiedIdentityAssertion:
    return verifier.verify(mint(key, base_claims(sub=subject)))


def _job_request(service: JobService) -> JobCreateRequest:
    track = service.corridor.tracks[0]

    return JobCreateRequest(
        track_id=track.track_id,
        job_type="BALLAST_TAMPING",
        distance_start=1_000.0,
        distance_end=2_000.0,
        workers_min=2,
        workers_max=4,
        description="slice 10.2d.2",
    )


@pytest.fixture()
def world(tmp_path, key, verifier):
    probe = JobService(repository=JobRepository(tmp_path / "probe.db"))
    corridor = probe.registry.corridor_id
    sections = sorted(probe.registry.section_ids())

    doc = {
        "schema": SCHEMA_MARKER,
        "version": 1,
        "people": [
            _person("P-ONE", SUB_ONE, ALIAS),
            _person("P-NARROW", SUB_NARROW, ALIAS),
            _person("P-MULTI", SUB_MULTI, ALIAS),
            _person("P-ADMIN", SUB_ADMIN, ALIAS),
        ],
        "role_assignments": [
            {"person_id": "P-ONE", "role": "WORKER"},
            {"person_id": "P-NARROW", "role": "WORKER"},
            {"person_id": "P-MULTI", "role": "WORKER"},
            {"person_id": "P-MULTI", "role": "ENGINEER"},
            {"person_id": "P-ADMIN", "role": "ADMIN"},
        ],
        "scope_assignments": [
            {"person_id": "P-ONE", "role": "WORKER", "corridor_id": corridor,
             "section_ids": sections},
            {"person_id": "P-NARROW", "role": "WORKER", "corridor_id": corridor,
             "section_ids": sections[:1]},
            {"person_id": "P-MULTI", "role": "WORKER", "corridor_id": corridor,
             "section_ids": sections[:1]},
            {"person_id": "P-MULTI", "role": "ENGINEER", "corridor_id": corridor,
             "section_ids": sections[1:2] or sections[:1]},
        ],
    }

    directory = load_identity_directory(write(tmp_path, doc))
    composed = compose_jobs(
        AuthMode.NOVAFORGE, directory=directory, db_path=tmp_path / "novaforge.db"
    )

    return types.SimpleNamespace(
        corridor=corridor, sections=sections, directory=directory, composed=composed,
        key=key, verifier=verifier, tmp_path=tmp_path,
    )


def _actor(world, subject: str, role=None) -> Actor:
    return authenticated_actor(
        verified(world.key, world.verifier, subject), world.directory, role
    )


def test_loaded_directory_composes_the_authenticated_policy(world):
    policy = world.composed.service.authorization

    assert type(world.directory) is IdentityDirectory
    assert type(policy) is AuthenticatedEnforcingPolicy
    assert world.composed.mode is AuthMode.NOVAFORGE
    assert (world.tmp_path / "novaforge.db").exists()


def test_verified_identity_resolves_through_the_loaded_directory_to_an_authenticated_actor(world):
    actor = _actor(world, SUB_ONE)

    assert type(actor) is Actor
    assert actor.actor_id == "P-ONE"
    assert actor.role is W
    assert actor.assurance is IdentityAssurance.AUTHENTICATED


def test_authenticated_in_scope_write_succeeds_on_the_tmp_database(world):
    job = world.composed.service.create_job(
        _job_request(world.composed.service), actor=_actor(world, SUB_ONE)
    )

    assert job["job_id"]


def test_authenticated_out_of_scope_is_denied(world):
    policy = world.composed.service.authorization
    actor = _actor(world, SUB_NARROW)

    policy.authorize_resource(
        actor, JobAction.REPORT_JOB, ResourceLocation(world.corridor, world.sections[0])
    )

    for location in (
        ResourceLocation(world.corridor, "NO-SUCH-SECTION"),
        ResourceLocation("OTHER-CORRIDOR", world.sections[0]),
    ):
        with pytest.raises(AuthorizationDenied):
            policy.authorize_resource(actor, JobAction.REPORT_JOB, location)


def test_a_person_absent_from_the_file_cannot_become_an_actor(world):
    with pytest.raises(DirectoryError) as info:
        _actor(world, SUB_STRANGER)

    assert info.value.reason == IDENTITY_NOT_ENROLLED


def test_a_declared_actor_with_an_enrolled_id_is_denied_by_the_floor(world):
    policy = world.composed.service.authorization
    declared = human_actor("P-ONE", W)

    with pytest.raises(AuthorizationDenied):
        policy.authorize(declared, JobAction.REPORT_JOB)
    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            declared, JobAction.REPORT_JOB, ResourceLocation(world.corridor, world.sections[0])
        )


def test_multiple_roles_still_require_an_explicit_selection_and_never_merge(world):
    with pytest.raises(AssignmentError) as info:
        _actor(world, SUB_MULTI)
    assert info.value.reason == ROLE_SELECTION_REQUIRED

    worker = _actor(world, SUB_MULTI, "WORKER")
    engineer = _actor(world, SUB_MULTI, E)
    assert (worker.role, engineer.role) == (W, E)

    assert world.directory.scopes_for_actor("P-MULTI", W) != world.directory.scopes_for_actor(
        "P-MULTI", E
    ) or len(world.sections) == 1

    with pytest.raises(AssignmentError):
        _actor(world, SUB_MULTI, A)


def test_a_loaded_admin_holds_no_scope_and_no_action(world):
    admin = _actor(world, SUB_ADMIN)
    policy = world.composed.service.authorization

    assert admin.role is ADM
    assert world.directory.scopes_for_actor("P-ADMIN", ADM) == ()

    for action in JobAction:
        with pytest.raises(AuthorizationDenied):
            policy.authorize(admin, action)


def test_novaforge_composition_still_requires_an_exact_identity_directory(tmp_path):
    with pytest.raises(AuthConfigurationError):
        compose_jobs(AuthMode.NOVAFORGE, directory=None, db_path=tmp_path / "x.db")

    with pytest.raises(AuthConfigurationError):
        compose_jobs(
            AuthMode.NOVAFORGE,
            directory=types.SimpleNamespace(scopes_for_actor=lambda *a: ()),
            db_path=tmp_path / "x.db",
        )


# ----------------------------------------------- 11. source-boundary checks

ALLOWED_OS_ATTRIBUTES = {
    "open", "fstat", "fdopen", "close", "PathLike", "fspath", "path",
    "O_RDONLY", "O_NONBLOCK", "O_BINARY", "O_CLOEXEC",
}
ALLOWED_GETATTR_CONSTANTS = {"O_NONBLOCK", "O_BINARY", "O_CLOEXEC"}
FORBIDDEN_NAMES = {
    "Actor", "ActorRole", "IdentityAssurance", "AUTHENTICATED", "SYSTEM_INTERNAL",
    "DECLARED_UNVERIFIED", "_AUTHENTICATION_CAPABILITY", "authenticated_actor",
    "VerifiedIdentityAssertion", "IdentityAssertionVerifier", "RecordedActor",
    "rehydrate_actor", "human_actor", "system_actor", "unidentified_actor",
    "environ", "getenv", "putenv", "unsetenv", "environb",
    "replace", "rename", "renames", "remove", "unlink", "rmdir", "truncate",
    "write", "writelines", "fsync", "fdatasync", "mkstemp", "mkdtemp",
    "NamedTemporaryFile", "TemporaryFile", "O_WRONLY", "O_RDWR", "O_CREAT",
    "O_TRUNC", "O_APPEND", "O_EXCL", "sqlite3", "subprocess", "socket", "pickle",
    "eval", "exec", "compile", "__import__", "importlib", "getcwd", "chdir",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "backend.app.api",
    "backend.app.jobs",
    "backend.app.audit",
    "backend.app.persistence",
    "backend.app.identity.actor",
    "backend.app.identity.authenticated",
    "backend.app.identity.identity_assertion",
    "backend.app.identity.recorded_actor",
    "backend.app.identity.trusted_keys",
    "backend.app.identity.policy",
    "backend.app.identity.resource",
    "sqlite3", "subprocess", "socket", "pickle", "importlib", "tempfile", "shutil",
)


def _docstring_constants(tree: ast.AST) -> set:
    return {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }


def test_loader_names_no_authentication_artifact_and_never_writes_or_reads_env():
    tree = ast.parse(LOADER_SOURCE)
    docstrings = _docstring_constants(tree)
    seen = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                seen.add(alias.name.split(".")[-1])
                seen.add(alias.asname or alias.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                seen.add(node.value)

    assert seen & FORBIDDEN_NAMES == set()


def test_loader_imports_only_what_the_gate_allows():
    tree = ast.parse(LOADER_SOURCE)
    modules = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")

    assert modules
    assert not [m for m in modules if m.startswith(FORBIDDEN_IMPORT_PREFIXES)]
    assert set(modules) == {
        "__future__", "os", "stat", "typing",
        "backend.app.identity._jose",
        "backend.app.identity.assignments",
        "backend.app.identity.directory",
        "backend.app.identity.person",
        "backend.app.identity.scope",
        "backend.app.identity.scope_assignment",
    }


def test_loader_uses_only_read_only_os_calls():
    tree = ast.parse(LOADER_SOURCE)
    used = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os"
    }
    assert used <= ALLOWED_OS_ATTRIBUTES

    getattr_constants = {
        n.args[1].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "getattr"
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
        and n.args[0].__class__ is ast.Name
        and n.args[0].id == "os"
    }
    assert getattr_constants <= ALLOWED_GETATTR_CONSTANTS

    fdopen_modes = [
        n.args[1].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "fdopen"
    ]
    assert fdopen_modes == ["rb"]

    builtin_open_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"
    ]
    assert builtin_open_calls == []


def test_loader_never_chains_the_constructors_exceptions():
    tree = ast.parse(LOADER_SOURCE)

    assert all(n.cause is None for n in ast.walk(tree) if isinstance(n, ast.Raise))
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        assert not [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]


def test_loader_is_not_exported_and_nothing_else_uses_it():
    init_source = (APP_ROOT / "identity" / "__init__.py").read_text(encoding="utf-8")
    assert "directory_file" not in init_source

    users = [
        str(path.relative_to(APP_ROOT))
        for path in APP_ROOT.rglob("*.py")
        if path != Path(loader_module.__file__)
        and "directory_file" in path.read_text(encoding="utf-8")
    ]
    assert users == []


def test_loader_result_type_and_error_type_are_the_only_outcomes(tmp_path):
    outcomes = []

    for data in (good_doc(), b"", b"{", {**good_doc(), "version": 2}, empty_doc()):
        try:
            outcomes.append(type(parse_identity_directory(data if isinstance(data, bytes) else encode(data))))
        except DirectoryFileError as error:
            outcomes.append(type(error))

    assert outcomes == [
        IdentityDirectory, DirectoryFileError, DirectoryFileError,
        DirectoryFileError, IdentityDirectory,
    ]


# ------------------------------------------- 12. demo and modes unchanged


@pytest.mark.parametrize("mode", [None, "demo"])
def test_demo_still_refuses_the_directory_variable(mode, tmp_path):
    environ = {"PASHUPAT_IDENTITY_DIRECTORY": str(write(tmp_path, good_doc()))}
    if mode is not None:
        environ["PASHUPAT_AUTH_MODE"] = mode

    with pytest.raises(AuthConfigurationError) as info:
        resolve_auth_mode(environ)
    assert info.value.reason == AUTH_CONFIG_IN_DEMO_MODE

    with pytest.raises(AuthConfigurationError) as info:
        compose_http_jobs(environ)
    assert info.value.reason == AUTH_CONFIG_IN_DEMO_MODE


def test_demo_still_refuses_a_loaded_directory(tmp_path):
    directory = load_identity_directory(write(tmp_path, good_doc()))

    with pytest.raises(AuthConfigurationError) as info:
        compose_jobs(AuthMode.DEMO, directory=directory, db_path=tmp_path / "demo.db")

    assert info.value.reason == DIRECTORY_IN_DEMO_MODE


def test_demo_composition_is_unchanged_and_unenforced(tmp_path):
    composed = compose_jobs(AuthMode.DEMO, db_path=tmp_path / "demo.db")

    assert composed.mode is AuthMode.DEMO
    assert type(composed.service.authorization) is not AuthenticatedEnforcingPolicy


def test_http_novaforge_boot_is_still_refused_and_never_reads_the_file(tmp_path):
    environ = {
        "PASHUPAT_AUTH_MODE": "novaforge",
        "PASHUPAT_IDENTITY_DIRECTORY": str(tmp_path / "does-not-exist.json"),
    }

    with pytest.raises(AuthConfigurationError) as info:
        compose_http_jobs(environ)

    assert info.value.reason == AUTHENTICATION_MECHANISM_UNAVAILABLE


def test_the_loader_ignores_the_environment(tmp_path, monkeypatch):
    other = write(tmp_path, empty_doc(), "other.json")
    path = write(tmp_path, good_doc())

    monkeypatch.setenv("PASHUPAT_IDENTITY_DIRECTORY", str(other))
    monkeypatch.setenv("PASHUPAT_AUTH_MODE", "novaforge")

    assert snapshot(load_identity_directory(path)) == snapshot(code_directory())


# ------------------------------------------------------------- hygiene


def test_services_in_this_module_run_only_on_tmp_databases(world):
    # The module-scoped fixture above proves the repository's jobs.db hash
    # is unchanged after the whole module; this proves the one database
    # the composed service was given is a tmp file, not the default.
    assert (world.tmp_path / "novaforge.db").exists()
    assert os.path.realpath(world.tmp_path) != os.path.realpath(REPO_JOBS_DB.parent)
