# Slice 10.1 — Railway Identity & Scope: Implementation-Readiness Audit

**Status:** READ-ONLY readiness audit. No implementation.
**Pashupatastra HEAD / origin/main:** `985ca045c2cc297f9f0dc0f33aaac2f20badb4c7`
**Parent gate:** `docs/SLICE10_IDENTITY_AUTHORIZATION_ARCHITECTURE.md` (Slice 10 verdict AMBER)
**NovaForge HEAD observed (read-only):** `5f3a2a6cd999286c032dce44a921132fe6d15e9f`
**Date:** 2026-09-21
**Verdict for Slice 10.1:** **GREEN** (with recorded defaults, §21). Slice 10 overall stays AMBER; its blocker is not in this slice's path.

---

## 1. Executive summary

Slice 10.1 needs **one new, purely additive Pashupatastra module family** and nothing else:

```
PERSON (external key -> stable person_id)
   -> RAILWAY ROLE ASSIGNMENT (person_id, ActorRole, scope)
        -> RAILWAY SCOPE (corridor_id + explicit set of section_ids)
             -> [later, 10.3] AUTHENTICATED ACTOR
```

The findings that decide the shape, each verified against code in this audit:

| # | Finding | Consequence |
|---|---|---|
| 1 | `Actor` has three fields, and `Actor.to_dict()` **is** the canonical event serialization (`events.py:317`; `canonical_bytes` `events.py:329-332`); `JobEvent.from_dict` rebuilds an `Actor` from exactly three fields (`events.py:345-348`). | **Scope must not be added to `Actor`.** It would change canonical event bytes and break event round-tripping. Scope travels *beside* the Actor, resolved from the directory. |
| 2 | `ActorRole` needs **no change**. `HUMAN_ROLES` (`actor.py:76-83`) is already the exact assignable set. | Zero new roles. |
| 3 | The topology is small: the checked-in dataset corridor `CORR-NDLS-AGC` has **6 sections** and **both tracks (UP-1, DOWN-1) serve all 6**; the **default** deployment corridor `CORRIDOR_A` has **1 section** (two isolated registry probes, §5.1). The deployment serves **one corridor**. | Track scope discriminates nothing. Corridor scope is degenerate today. Scope = `corridor_id` + explicit `frozenset[section_id]`. No hierarchy. |
| 4 | `section_id` is **guaranteed only for jobs created through `create_job`** (`resolve_job_resource` fails closed). It is `Optional` on `BlockProposal`, `ExecutionRecord`, `PossessionWindow`, and the dataclass `ScheduledBlock`. **The live `jobs.db` holds one job whose `block_candidate_json` has no `section_id` key at all.** | "Missing section_id silently becoming global access" is not hypothetical. Scope matching must be a multi-valued result, never a bool. |
| 5 | The seam's resource parameter has **three shapes**, not one: single-job, not-yet-resolved (`REPORT_JOB`), and collection/corridor-wide (§13). | One `Resource` type is wrong. |
| 6 | **8 test-double policies in 6 test files implement the 2-argument `authorize(actor, action)`.** | The parent doc's claim that an optional third parameter "leaves the existing denying test policy working" is **false** as soon as a call site passes it. The seam must grow by a *second, optional* method, not a wider signature (§13). |
| 7 | `job_events` carries **no person key except `actor_id`**. Existing ids are role-prefixed (`WORKER-042`). | For authenticated persons, `actor_id` must be **role-neutral and stable across a person's roles**, or SoD is unanswerable for multi-role people. This is the single most important identity constraint. |
| 8 | Nothing in 10.1 needs an assignment to change *without a redeploy*. | **No database.** A validated, immutable, config-seeded directory behind a Protocol. |
| 9 | 10.1 can be built without editing `actor.py`, `authorization.py`, `deps.py`, any router, any schema, OpenAPI, `jobs.db`, or any existing test. | The **three** tests pinning `IdentityAssurance` membership cannot be disturbed. |

**Verdict: GREEN.** Every decision below lands on repository evidence. The unknowns (§21) are deferrable to 10.5 / 10.6 or have a safe default that is reversible without migration.

---

## 2. Current domain inventory

### 2.1 Numbering note — read this first

The parent doc §20 numbered sub-slices *10.1 Assertion verification, 10.2 Domain identity directory, 10.3 AUTHENTICATED actor, 10.4 Audit metadata, 10.5 Scope + enforcing policy, 10.6 NovaForge integration*. The brief for this audit re-scopes **10.1 = railway identity & scope** ("PERSON -> ROLE -> SCOPE before Actor"). That is parent-10.2 plus the *data* half of parent-10.5. The reordering is correct (the parent doc itself said the evidence inverts the brief's order for the directory). Proposed renumbering, used in the rest of this report:

| New | Content | Parent |
|---|---|---|
| **10.1** | Railway identity & scope foundation (this audit) | 10.2 + scope data of 10.5 |
| 10.2 | Assertion verification seam (`VerifiedPrincipal`) | 10.1 |
| 10.3 | `AUTHENTICATED` assurance + authenticated Actor + `request_actor` replacement | 10.3 |
| 10.4 | Authentication-aware audit metadata | 10.4 |
| 10.5 | Resource-aware enforcing policy + call-site enrichment | 10.5 |
| 10.6 | SoD rule | (parent D-3) |
| 10.7 | NovaForge integration (BLOCKED on OQ-1) | 10.6 |

### 2.2 Corrections to the parent doc (evidence-based)

| Parent statement | Finding |
|---|---|
| "19 call sites verified" (§2.6) | **16 real `authorize()` calls**: 15 in `service.py` (lines 761, 1308, 1341, 1469, 1518, 1567, 1608, 1676, 1738, 1801, 1894, 1966, 2021, 2083, 2203) + 1 in `optimization.py:413`. `grep '\.authorize('` over `backend/app` returns 17 matches; the 17th is prose at `service.py:479`. The parent's own list enumerates 16. |
| An optional context parameter "leaves ... the existing denying test policy working" (§10.3) | Only if no call site passes it. 8 test doubles define `authorize(self, actor, action)`: `test_job_lifecycle_accountability.py:1274` (`DenyPolicy`), `test_slice3_proposal_review.py:185,891`, `test_slice5_step3_execution_service.py:210`, `test_slice5_step4_evidence_gated_completion.py:675`, `test_slice6_authority_release.py:476,487`, `test_slice9_accountability_api.py:593`. A call `authorize(a, act, resource)` raises `TypeError` on every one. See §13. |
| "Division — No" (§9.1) | `Corridor.division` exists (`data/models.py:284`, default `"Delhi"`; dataset value `"Delhi / Agra"`; loaded `corridor_dataset.py:209`). It is a **descriptive string**: no `Division` model, no division->sections mapping anywhere. The conclusion (no division scope unit) stands; the evidence line was imprecise. |
| OQ-2 as a broad "which section governs" question | Narrower than stated. See §6.4. |
| "Two tests pin the `IdentityAssurance` enum" (§2.1, §20 sub-slice 10.3) | **Three** do: `test_actor_identity.py:86`, `test_slice7_contract_freeze.py:745`, and `test_slice9_safety.py:368` (`test_no_authenticated_assurance_level_was_introduced`). Sub-slice 10.3 must update all three together. Found during final verification; the parent doc and this report's first draft both said two. |

### 2.3 Inventory: what can participate in PERSON / ROLE / SCOPE

| Object | Location | PERSON | ROLE | SCOPE | Notes |
|---|---|---|---|---|---|
| `Actor` (`actor_id`, `role`, `assurance`) | `identity/actor.py:115-215` | `actor_id` only | `role` | -- | Frozen; also the canonical event actor. **Do not extend.** |
| `ActorRole` | `actor.py:54-73` | -- | Yes | -- | `WORKER ENGINEER AUTHORITY ADMIN SYSTEM UNIDENTIFIED` |
| `HUMAN_ROLES` | `actor.py:76-83` | -- | **Assignable set** | -- | `{WORKER, ENGINEER, AUTHORITY, ADMIN}` |
| `IdentityAssurance` | `actor.py:86-97` | -- | -- | -- | No `AUTHENTICATED`; pinned by **three** tests (`test_actor_identity.py:86`, `test_slice7_contract_freeze.py:745`, `test_slice9_safety.py:368`). **Untouched in 10.1.** |
| `_ACTOR_ID_PATTERN` | `actor.py:108` | Id validity rule | -- | -- | `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`; `SYSTEM*` reserved (`actor.py:157-178`) |
| `JobAction` (15 members) | `identity/authorization.py:34-80` | -- | -- | -- | Complete; no new member needed |
| `AuthorizationPolicy` / `UnenforcedPolicy` | `authorization.py:100-124` | -- | -- | -- | `authorize(actor, action)` only |
| `request_actor` | `api/deps.py:33-95` | declared id | declared role | -- | Only external identity entry point |
| `SectionRegistry` | `data/section_registry.py:121-410` | -- | -- | **Authority for section identity** | Per-corridor; `section_ids()`, `get()`, `has()`, `track_ids_for()`, `resolve_by_chainage()` |
| `Section` | `data/models.py:192-277` | -- | -- | Unit | `section_id` opaque; `corridor_id`; `track_ids` |
| `Corridor` | `data/models.py:279-307` | -- | -- | Container | `corridor_id`; `zone`/`division` are strings |
| `TrackSegment` | `data/models.py:115-160` | -- | -- | Not a scope unit | Deliberately has **no** `section_id` (`models.py:119-128`) |
| `Asset` | `data/models.py:60-113` | -- | -- | Not a scope unit | Has `track_id`, `km_location`; **no** `section_id`/`corridor_id` field. Section is derived (`asset_association.py`) |
| Job row `maintenance_jobs` | `jobs/repository.py:127-153` | -- | -- | Via JSON | **No section column**; section lives in `block_candidate_json["section_id"]` |
| `BlockCandidate` (jobs) | `jobs/service.py` step 4 (~940-966) | -- | -- | `section_id` | Set from `resolve_job_resource` for new jobs |
| `BlockProposal` | `jobs/proposal.py:85-99` | -- | -- | `Optional[str]` | Copied from block (`proposal.py:366`) |
| `ExecutionRecord` | `jobs/execution.py:580-596` | -- | -- | `Optional[str]` | Copied from block (`execution.py:210`) |
| `PossessionWindow` | `contracts/schemas.py:88-125` | -- | -- | `Optional[str]` | Solver input, not an authorization resource |
| `ScheduledBlock` | `contracts/schemas.py:205` / `contracts/common.py:84` | -- | -- | dataclass: `Optional[str]`; **pydantic: none** | Two different classes; neither is an authorization resource |
| `JobObligation` | `jobs/obligations.py:241` | -- | `owed_role` | **none** | Derived from the job; carries no section/corridor |
| `job_events` | `jobs/history.py:57-80` | `actor_id` | `actor_role` | -- | `metadata_json TEXT NOT NULL` exists for additive facts |
| `optimization_runs` | `audit/models.py:52` | -- | -- | `corridor_id: Optional[str]` | Run-level; not section-scoped |
| `NotificationIntent.recipient_role` | `jobs/notifications.py:199` | -- | Role only | -- | No individual recipient; preserved |
| Repository / service layer | `jobs/repository.py`, `jobs/service.py` | -- | -- | -- | Authorization calls precede most reads |

### 2.4 Completely missing

- Any **person** record or external-identity reference.
- Any **assignment** of a role to anything.
- Any **scope** value object.
- Any **resource reference** type usable by authorization.
- Any **demo-vs-trusted** distinction beyond `assurance`.

Confirmed by search: no `assigned_to`, `assignee`, `crew`, `roster`, `responsible_for`, `owner_id`, `actor_section`, `actor_scope` in `backend/app/`; and `division`/`zone` appear only as descriptive `Corridor` strings.

---

## 3. Person boundary

**Pashupatastra needs a stable external subject reference and a Pashupatastra-owned person id. Nothing else.**

### 3.1 Minimum representation

```
ExternalIdentity:  identity_provider (str, logical alias e.g. "novaforge")
                   subject           (str, the provider's stable user id)
Person:            person_id         (str, Pashupatastra-owned, role-neutral)
                   identity          (ExternalIdentity)
```

### 3.2 Decisions, each with evidence

| Question | Decision | Evidence |
|---|---|---|
| Stable identity key | `(identity_provider, subject)` | NovaForge `sub` = `User.id`, `@default(uuid())` (`NovaForge/server/prisma/schema.prisma:55`). Never email. |
| Is issuer required? | **Yes, but as a logical `identity_provider` alias, not a raw URL in each row.** The trusted issuer URL is pinned once in configuration; the alias maps to it. | NovaForge's issuer is `process.env.IDENTITY_ISSUER ?? env.rpOrigin` (`identityAssertion.ts:16`) and `rpOrigin` defaults to a deployment URL (`lib/env.ts:24`). **The issuer string follows the deployment origin and can change.** Storing it per assignment would orphan every assignment on a URL migration; an alias survives it. |
| Globally unique subject? | Unique **within an issuer**; assume nothing across issuers. | A uuid PK is unique in NovaForge's DB only. A composite key costs nothing and forecloses collision if a second provider ever exists. |
| Composite key required? | **Yes** | Above. |
| Display name? | **No** | `User.name` is not unique and not stable; not needed for any decision. |
| Email? | **No** | NovaForge `email` is `@unique` but mutable PII. Assignment by email/name is a listed red line (§16). |
| Personal profile data? | **Stays entirely in NovaForge.** | Pashupatastra stores a reference, not a replica (parent §6 rule 4). |
| Human-readable label for audit UI? | **Deferred** | Needs a NovaForge lookup or an HR id; neither exists. `person_id` is opaque but stable. |

### 3.3 `person_id` is the audit key, and must be role-neutral

`job_events.actor_id` (`history.py:66`) is the only person key in the audit trail. The existing convention (`WORKER-042`, `AUTHORITY-017`; `actor.py:11`, `deps.py:507`) **embeds the role in the id**. If one human holds two roles and receives two role-prefixed ids, "did the same person report this job and approve its proposal?" cannot be answered from `job_events` at all.

**Constraint (binding on 10.1):**

1. `person_id` is **opaque and role-neutral** (illustrative: `P-000123`). It must **not** encode a role.
2. It is **stable across all of a person's role assignments** and **never reused**.
3. **`(identity_provider, subject)` <-> `person_id` is a bijection.** Unique on both sides. One human with two `person_id`s defeats SoD; two humans sharing one is an impersonation vector.
4. It must satisfy the existing `Actor` id rule and the `SYSTEM*` reservation. **10.1 reuses that rule without editing `actor.py`** by *validating through the public `human_actor(person_id, ActorRole.WORKER)` and discarding the result* inside a single private helper (`_validate_person_id`). This is the **only** place a 10.1 module touches an `Actor`, and the value never leaves that helper: it is neither returned, stored, nor exposed. It keeps one source of truth (no duplicated regex; future changes to the id rule or the `SYSTEM*` reservation propagate automatically). The cleaner long-term fix is a public `validate_actor_id()` in `actor.py`, **deferred to 10.3** because 10.1 must not edit `actor.py`.

Why not use the NovaForge `sub` directly as `actor_id`? A uuid (36 chars) would fit the 64-char pattern, but it couples the railway audit key to an external system, is unreadable in history, and is provider-bound. The parent doc §13.1 already reached this conclusion; this audit adds the role-neutrality requirement.

Side effect worth knowing: idempotency is scoped to `(actor, idempotency key)` (`events.py:386` docstring). A role-neutral `person_id` means one person retrying under a different role shares the key scope. That is arguably correct (same person, same retry) and is noted as a behavior to confirm in 10.3, not a 10.1 concern.

**Legacy/demo ids** (`WORKER-042`) remain valid for `DECLARED_UNVERIFIED` actors and are not touched. They carry no cross-role identity guarantee, which is why SoD is meaningful **only** for authenticated actors (§10).

---

## 4. Railway role model

`ActorRole` stays **unchanged**. No role is added.

| Role | Class | Assignable to a person? | Scope requirement | Evidence |
|---|---|---|---|---|
| `WORKER` | Human operational | Yes | **Non-empty** section set | Executes work (`authorization.py:5-9`) |
| `ENGINEER` | Human operational | Yes | **Non-empty** section set | Plans/replans |
| `AUTHORITY` | Human operational | Yes | **Non-empty** section set | Approves/commits/releases (`lifecycle.py:1401,1808`) |
| `ADMIN` | **Administrative**, not operational | Yes | **No operational scope** (`scope = None`) | The module docstring defines it as "manages identities" (`authorization.py:16-17`). Parent §8.2/§11.1: no operational scope, and never derived from NovaForge `ADMIN` |
| `SYSTEM` | System | **Never** | -- | In-process only; `Actor` already refuses `SYSTEM*` ids for humans (`actor.py:161-178`) and HTTP refuses it (`deps.py:84-90`) |
| `UNIDENTIFIED` | Compatibility marker | **Never** | -- | "The honest current reality that a caller supplied no identity" (`actor.py:61-65`); never client-selectable |

**Assignment rule (10.1):** a `RoleAssignment` may only carry a role in `HUMAN_ROLES`. Constructing one for `SYSTEM` or `UNIDENTIFIED` raises. `ADMIN` must have `scope = None`; the three operational roles must have a non-empty scope. This encodes "ADMIN gains no operational approval" structurally: an ADMIN assignment cannot cover any section, so no future scope match can succeed for it.

**Whether ADMIN may *read* anything** (e.g. history) is a 10.5 policy decision, not a 10.1 data decision. The data model simply cannot make ADMIN operationally scoped.

**ENGINEER** is deliberately *not* special-cased: "multiple sections" is just a larger `section_ids` set.

---

## 5. Railway scope model

### 5.1 Topology evidence (isolated, read-only probe)

`load_corridor_dataset()` was run with `PASHUPAT_JOBS_DB` pointed at a non-existent temp path (no DB file was created; `jobs.db` hash re-verified unchanged):

```
CORR-NDLS-AGC  division="Delhi / Agra"  zone="Northern Railway / NCR"
NDLS-NZM  UP-1,DOWN-1   0.0-7.0
NZM-FDB   UP-1,DOWN-1   7.0-32.0
FDB-PWL   UP-1,DOWN-1   32.0-66.0
PWL-MTJ   UP-1,DOWN-1   66.0-133.0
MTJ-RKM   UP-1,DOWN-1   133.0-190.0
RKM-AGC   UP-1,DOWN-1   190.0-195.0        len(registry) == 6
```

Facts that bound the design:

- **Track discriminates nothing.** Both tracks serve every section. A track-level scope would be a no-op today, and `resolve_job_resource` already validates that the job's track serves the section (`resource_resolution.py`).
- **The deployment is single-corridor.** `JobService` serves exactly one corridor and rejects a mismatching `corridor_id` with 400 (`service.py:784-795`). So a corridor-level scope is **identical to "all sections"**.
- **Section ids are conventions, not identity** (`format_section_id`: "a NAMING CONVENTION, not the identity ... a corridor whose sections come from a real source may carry entirely different strings", `section_registry.py:66-83`). Generated corridors use corridor-prefixed ids; the dataset uses bare station pairs.
- The brief's illustrative "Section S3" corresponds to the third registered section, `FDB-PWL`.

**Which corridor is the deployment default? Not this one.** `DEFAULT_CORRIDOR_ID = os.getenv("PASHUPAT_CORRIDOR_ID", "CORRIDOR_A")` (`service.py:175`). A second isolated probe (`JobService._build_default_corridor_and_registry()` with `PASHUPAT_JOBS_DB` pointed at a non-existent temp path; no DB file created) shows the **default `CORRIDOR_A` has exactly ONE section**, `CORRIDOR_A_S0-CORRIDOR_A_S1`, served by `UP-1` and `DOWN-1`. The 6-section `CORR-NDLS-AGC` registry above is reached **only** when `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC`. Consequences: (a) on the default corridor a scope can only ever be "that one section", so `NOT_COVERED` is **unreachable** there, and scope is indistinguishable from corridor scope; (b) any demonstration or test of section-scoped enforcement (10.5) must run on the dataset corridor; (c) the 6-section topology is the realistic design target, which is why the model is a section set and not a boolean. The design conclusions (no track scope, no hierarchy, explicit ids) hold on both.

### 5.2 Decision: the smallest scope abstraction

```
RailwayScope:
    corridor_id   str
    section_ids   frozenset[str]     # explicit, non-empty for operational roles
```

- **No hierarchy.** Corridor -> section -> track buys nothing on this topology and would invite the unsafe inheritance the brief warns about.
- **No wildcard.** "Corridor scope" is a *convenience at assignment authoring time*: a helper expands a corridor into `registry.section_ids()` and the **explicit ids are stored**. A section added to the registry later is therefore **not** silently covered (fail-closed), whereas a stored wildcard would silently cover it. This directly answers "do not assume corridor scope = permission over every future object": the topology makes wildcard inheritance *unsafe*, because the registry can be regenerated (assets are regenerated on every `JobService` construction; `asset_association.py` module doc, "REFERENCE INTEGRITY").
- **Corridor-qualified.** Scope carries `corridor_id` once, because section ids are only unique *by convention* within a corridor registry. It is cheap, matches the single-corridor service, and prevents a future cross-corridor id collision from becoming cross-corridor authority. A person covering two corridors holds two assignments.
- **Validated against the registry at load time.** Every `section_id` must exist in the `SectionRegistry` for its `corridor_id` (`registry.has()`); an unknown id **refuses the load** (a typo must never become a silently match-nothing assignment).
- **Not a scope unit:** track (no discrimination), asset (no `section_id` field, regenerated identity, fingerprint-only), division (a string), maintenance jurisdiction / crew (no concept; parent OQ-3, deferred).

### 5.3 What is safely inherited

Nothing is inherited. Scope membership is exact set membership on `(corridor_id, section_id)`. This is the conservative reading and the only one the topology supports safely.

---

## 6. Resource-scope matching

### 6.1 Where a section is available, and where it is not

| Resource | `section_id` | Guaranteed? | Evidence |
|---|---|---|---|
| Job created by `create_job` | `block_candidate["section_id"]` | **Yes, for new jobs.** `resolve_job_resource` resolves both endpoints and refuses a straddling job, an unresolvable location, or a track that does not serve the section. | `resource_resolution.py:65-143`; `service.py:831-870` |
| **Legacy persisted job** | key absent | **No.** The live `jobs.db` has 1 job whose `block_candidate_json` has no `section_id` key (read-only, `mode=ro&immutable=1`; hash unchanged) | Appendix A |
| `JobCreateRequest` | none | **No.** Carries `track_id` + distances; section is *derived* | `models.py:61-153` |
| `BlockProposal` | `Optional[str]` | No | `proposal.py:95,366` |
| `ExecutionRecord` | `Optional[str]` | No | `execution.py:593,210` |
| `PossessionWindow` | `Optional[str]` | No | `contracts/schemas.py:112` |
| `ScheduledBlock` | dataclass `Optional`; pydantic version none | No | `schemas.py:205-213`; `common.py:84-90` |
| `Asset` | **no field**; derived `asset_section_id` recorded in job metadata | No | `models.py:60-113`; `asset_association.py` |
| `JobObligation` | none; derived from job | No | `obligations.py:241` |
| `OptimizationRun` | `corridor_id: Optional[str]` | No | `audit/models.py:52` |

`lifecycle.py` already reads the section defensively everywhere (`(job.get("block_candidate") or {}).get("section_id")` at `1169, 1860, 2141`), i.e. the code base already treats absence as possible.

**Rule:** the **job's** `block_candidate["section_id"]` is the single scope key for job-shaped actions. Proposals/executions are derived from the same block; they are never a second source of truth. **An absent/`None` section is `UNRESOLVED`, and a scoped role is denied.** It never means "global".

### 6.2 Worked examples (the brief's questions)

Assume AUTHORITY assigned `{FDB-PWL}` and WORKER assigned `{FDB-PWL}` in corridor `CORR-NDLS-AGC`.

| Question | Section source | Result under a scope-aware policy |
|---|---|---|
| AUTHORITY approve job with section `FDB-PWL` | job block section | scope covers -> permitted (role/state/SoD decided elsewhere) |
| ... section `NZM-FDB` | job block section | `NOT_COVERED` -> deny |
| ... legacy job, no section | absent | `UNRESOLVED` -> deny |
| AUTHORITY release committed block on `FDB-PWL` | job (`notified`) | covers -> permitted; state decided elsewhere |
| AUTHORITY view obligations on `FDB-PWL` | **single**: job section. **list**: no single job | single: gate. list: **filter**, see §13 |
| WORKER execute (start) job on `FDB-PWL` | job section | covers |
| WORKER complete job on `FDB-PWL` | job section | covers |
| WORKER create job on `FDB-PWL` | **not known until resolved** | see the ordering conflict, §13.2 |

### 6.3 Gaps identified

1. **No section column on `maintenance_jobs`.** Scope *filtering in SQL* (list endpoints) is a separate problem from per-request matching; it must filter in Python over `block_candidate_json` or add a column later. Out of 10.1.
2. **`REPORT_JOB` authorizes before the section exists** (§13.2).
3. **Legacy rows** lack a section (empirical).
4. **`READ_OPTIMIZATION_RUN`** targets a run whose `corridor_id` is `Optional`; corridor-level, not section-level.

### 6.4 OQ-2 narrowed, and shown to be non-blocking

Parent OQ-2 asked which section governs a cross-section asset association. Evidence narrows it:

- A job's own section is **never ambiguous**: `resolve_job_resource` refuses a job whose endpoints resolve to different sections.
- The block is keyed `(track_id, section_id)` and the solver matches possession windows to the block on exactly that pair (`resource_resolution.py` module doc; `section_registry.py` module doc). **The thing an AUTHORITY approves is possession for the block's section**, which is the job's section.
- What survives is only `asset_section_id != job_section_id`, which `asset_association.py` records as `section_match` and already lets a deployment refuse at report time via `AssetAssociationPolicy.require_same_section`.

**Conclusion:** for 10.1 no ruling is needed. The recommended default for 10.5 is "authorize on the job/block section; `require_same_section=True` in enforcing deployments so authorization never faces a mismatch". Whether a *both-sections* rule is wanted is a domain-safety question that only bites at 10.5. **It does not block 10.1.**

---

## 7. Multi-role model

### 7.1 Domain representation

```
Person P-000123
  assignments (persistent in the directory, immutable per process):
     ENGINEER  -> CORR-NDLS-AGC {NZM-FDB, FDB-PWL, PWL-MTJ}
     AUTHORITY -> CORR-NDLS-AGC {FDB-PWL}
  active role for this request: AUTHORITY      <- selected, never asserted
```

- **Where assignments live:** the `AssignmentDirectory` (10.1C), behind a Protocol; static, config-seeded implementation now.
- **Persistent?** Yes across requests (they are directory data); **immutable within a request** (frozen value objects from an immutable snapshot).
- **Uniqueness invariant:** at most one assignment per `(person_id, role)`. Multiple sections are one assignment with a larger set. This removes "overlapping assignments" entirely rather than defining merge semantics.

### 7.2 One active role per request — selection

The `Actor` model holds one role (`actor.py:125`) and the event schema is built on that; the parent doc §8.4 settled option (i). 10.1 defines the *selection function*, not the transport:

```
select_active_assignment(person_assignments, requested_role | None) -> ActiveAssignment

  requested_role given:
      not in HUMAN_ROLES            -> reject (INVALID_ROLE)
      not held by the person        -> reject (ROLE_NOT_ASSIGNED)
      held                          -> that assignment
  requested_role absent:
      exactly one assignment        -> that assignment
      zero assignments              -> reject (NO_ASSIGNMENT)
      more than one                 -> reject (ROLE_SELECTION_REQUIRED)
```

**Never** pick "first", "highest" or "most privileged". Ambiguity is a rejection.

### 7.3 The trust rule

`requested_role` is a **selector among roles the directory already says the person holds**. It is not a claim, and its value confers nothing. `X-Actor-Role: AUTHORITY` from a client is still untrusted input; it can *only* choose an assignment that exists. The returned role always comes from the directory, never from the input. This resolves parent OQ-4 without a session-state role switch: selection is per request and stateless. Deriving the role from `(action, resource)` instead of a selector is a 10.5 refinement (it needs the action->roles table, which is policy).

### 7.4 Mapping future NovaForge claims

`VerifiedPrincipal(iss, sub, sid, ...)` (10.2) -> provider alias via pinned issuer -> `Person` lookup by `(provider, sub)` -> assignments -> selection -> `ActiveAssignment`. The NovaForge `role` claim is **recorded only, never an input to any step** (parent §8.2).

---

## 8. Role-scope assignment model

### 8.1 Representation

```
RoleAssignment:
    person_id   str
    role        ActorRole              # in HUMAN_ROLES only
    scope       RailwayScope | None    # None iff role is ADMIN
    basis       AssignmentBasis        # DIRECTORY | DEMO_DECLARED

AssignmentDirectory (Protocol, read-only):
    person_for(identity: ExternalIdentity) -> Person | None
    assignments_for(person_id) -> tuple[RoleAssignment, ...]
    assignment_for(person_id, role) -> RoleAssignment | None
```

### 8.2 The brief's examples

| Person | Role | Scope |
|---|---|---|
| U123 | AUTHORITY | `CORR-NDLS-AGC {FDB-PWL}` |
| U456 | WORKER | `CORR-NDLS-AGC {FDB-PWL}` |
| U789 | ENGINEER | `CORR-NDLS-AGC {NZM-FDB, FDB-PWL, PWL-MTJ}` |

### 8.3 Currently required vs deferred

| Capability | Now? | Reasoning |
|---|---|---|
| One role, multiple sections | **Required** | ENGINEER example |
| Multiple roles, different scopes | **Required** | Parent §8.4 |
| Overlapping assignments | **Eliminated** | `(person, role)` unique |
| Inactive/suspended assignment flag | **Deferred** | A person-level kill switch already exists in NovaForge (account status is checked per assertion and by introspection); assignment removal = config change. See §9 |
| Expiry / `effective_from/until` | **Deferred** | §9 |
| Assignment versioning / change history | **Deferred** | Needs a mutable store and an audited admin surface (parent D-2/OQ-8) |
| Crew / division / jurisdiction | **Deferred** | No concept; corridor + explicit sections suffice (parent D-6, OQ-3) |
| Delegation / acting-in-role | **Deferred** | Not evidenced |

---

## 9. Temporal validity decision

**Deferred.** `effective_from` / `effective_until` are **not** added in 10.1.

Reasoning:

1. **No demo requirement.** The demo documents specify no actors at all (zero matches for any actor identifier or header in `docs/demo-script.md`, `docs/demo-runbook.md`, `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md`; parent §12.2). `X-Actor` appears only in backend code and tests.
2. **The directory is static per process** in 10.1. A time window on immutable data adds a clock dependency and a semantics question (evaluate at *request* time or *event* time?) with no consumer.
3. **A person-level kill switch already exists** independently of assignments: NovaForge refuses to issue an assertion for a non-`ACTIVE` user and introspection re-checks the session (`identityAssertion.ts:20`, `internal.ts:74`). So loss of employment/access is handled at the identity layer; assignment expiry would only add *role narrowing over time*.
4. **Additive later.** `RoleAssignment` is a frozen dataclass; adding optional window fields later is compatible and needs no data migration in a config-seeded directory.

**Accepted risk (record it):** until temporal validity or an admin surface exists, *narrowing* a person's role/scope requires a configuration change and redeploy. Acceptable for pilot/demo; must be revisited before any multi-week production assignment lifecycle.

**Test to add when it is introduced:** stale-assignment rejection (§17).

---

## 10. SoD requirements

10.1 does **not** implement SoD. It must preserve what SoD needs:

1. **The comparison key is `actor_id`** in `job_events`, because that is the only person key the history holds (§3.3).
2. **Role-neutral, stable `person_id`** across a person's roles, so REPORT-by-person-X and APPROVE-by-person-X compare equal even when the roles differ.
3. **Bijection `(provider, subject)` <-> `person_id`** (§3.3) so one human cannot appear as two people.
4. **SoD is only meaningful for authenticated actors.** Facts in the current code that a comparator must respect:
   - `create_job` is deliberately unguarded (`lifecycle.py:1908`), so a reporter may legitimately be `UNIDENTIFIED` with `actor_id == "UNIDENTIFIED"` (`actor.py:112`). **All anonymous reporters share one id**, so an id-equality test would call them "the same person". A comparator must return **UNKNOWN**, not equal/not-equal, for any non-`AUTHENTICATED` side.
   - `DECLARED_UNVERIFIED` ids are self-typed; one human can declare two. Not comparable.
5. Which pairs must differ was analysed in the parent §12.3: REPORT != APPROVE and APPROVE != EXECUTE strongly; EXECUTE/COMPLETE and REPORT/EXECUTE not required. That table is not re-litigated here.

10.1 acceptance includes tests that the identity layer *provides* the bijection and role-neutrality; the comparison rule itself ships with 10.6.

---

## 11. Database decision

**Option A/D: no DB change.** Domain objects plus a validated, immutable, config-seeded `StaticAssignmentDirectory` behind an `AssignmentDirectory` Protocol.

### 11.1 The discriminating question

> Does anything in 10.1 require an assignment to change without a redeploy?

**No.** 10.1 defines concepts and invariants. There is no admin surface (deferred, parent D-2; the bootstrap/administrator question is parent OQ-8), no consumer that mutates assignments, and no request path that reads the directory yet. A table would be persistence with no writer.

### 11.2 Factor-by-factor

| Factor | Assessment |
|---|---|
| Current SQLite architecture | `jobs.db` holds `maintenance_jobs`, `optimization_runs`, `job_events`. Adding identity tables mixes operational and identity data in the file the demo resets and the tests isolate. |
| Production eventual DB | The Protocol lets a table-backed implementation replace the static one without touching consumers. |
| Authentication source | NovaForge owns credentials; the directory owns only the railway binding. |
| NovaForge ownership | Untouched. |
| Auditability | Assignment *changes* need their own audit trail; that is a full slice with its own authorization problem (parent D-2). A static file audited via git is honest for now. |
| Future assignment changes | Handled by swapping the Protocol implementation. |
| Test isolation | An in-memory static directory needs no DB isolation; `conftest.py` already isolates `jobs.db`. No new isolation surface. |
| Frozen v1 API | Unaffected. |
| Duplicate identity systems | Avoided: no accounts, no credentials, no emails. |

Also noted: `maintenance_jobs` has no section column, so any future scope *filtering* in SQL is a separate problem from 10.1's per-request resolution.

### 11.3 Seeding and failure posture

- **Loaded from a deployment-supplied file** (path via configuration). A small, clearly labelled **demo fixture** may be checked in; a **production** file is supplied by the deployment.
- **Missing or empty file in production mode = empty directory = every authenticated principal gets "no assignment" (403 class in 10.3)**. Never a default role.
- **Load-time validation** (refuse to start on any violation): unknown section for its corridor; role not in `HUMAN_ROLES`; operational role with empty scope; ADMIN with a scope; duplicate `(person, role)`; duplicate `(provider, subject)`; duplicate `person_id`; invalid or `SYSTEM*` `person_id`; unknown fields (in particular any `novaforge_role`, `email`, `name` key is rejected, so PII and NovaForge roles cannot creep in).
- **No secrets** in the file. No tokens. No emails.

### 11.4 If persistence is later required (conceptual only; do not create)

```
person            (person_id PK, identity_provider, subject, UNIQUE(identity_provider, subject))
role_assignment   (person_id, role, corridor_id, UNIQUE(person_id, role, corridor_id))
assignment_scope  (assignment id, section_id, UNIQUE(assignment id, section_id))
assignment_event  (append-only; who changed what, when)   -- the audit trail
```

Plus the append-only guard pattern already in `persistence/append_only.py`. Not part of 10.1.

---

## 12. API contract impact

**None.** No endpoint, no request/response model, no header, no error code, no OpenAPI change.

- 10.1 adds no router and no model that any router imports, so the frozen v1 surface cannot move.
- `X-Actor-Id` / `X-Actor-Role` behavior in `request_actor` is untouched. A role *selector* input for multi-role persons is a 10.3 concern (it arrives with the authenticated request path).
- New 403/401 error codes belong to 10.3/10.5.

**Acceptance:** the contract-freeze suite (`test_slice7_contract_freeze.py`) and OpenAPI comparison pass **unmodified**.

---

## 13. Authorization seam preparation

### 13.1 Three resource shapes, from the call sites

| Shape | Call sites | Scope key |
|---|---|---|
| **Single-job** | `service.py:1308` history, `1341` proposal, `1469` obligation (one job), `1608` commit, `1676` reject, `1738` postpone, `1801` release, `1894` start, `1966` complete, `2021` not-completed, `2083` execution read, `2203` assign schedule | job's `block_candidate["section_id"]` (+ job status for 10.5 state rules) |
| **Not resolved yet** | `service.py:761` `REPORT_JOB` | section is derived at step 3.5, *after* authorize |
| **Collection / corridor-wide** | `service.py:1518` obligation **list** (pages across active jobs, no `job_id`); `service.py:1567` `READ_OPTIMIZATION_RUN` (run-level, `corridor_id` optional); `optimization.py:413` `optimize_corridor` (corridor) | a corridor, or a scope **filter** over results |

**So the answer to "does each action need a different resource type" is yes: three shapes.** A single `Resource` type would force `section_id: Optional[...]` everywhere and re-create the "None means global" hazard.

### 13.2 The `REPORT_JOB` ordering conflict (named explicitly)

The seam's contract is that authorization runs "BEFORE it reads or writes anything" so a denial leaves no state and no event (`authorization.py:1-9`; proved by `DenyPolicy` tests). But `REPORT_JOB` is authorized at `service.py:761`, and its section is only known after `resolve_job_resource` (`service.py:831-870`). Options for 10.5:

- (a) **Move the `REPORT_JOB` authorize call after the pure resolution step.** `resolve_job_resource` reads only the registry and writes nothing, so "no state, no event" is preserved. Cost: an unauthorized caller sees validation 400s before 403 for malformed locations (low sensitivity).
- (b) Authorize twice (role first with no resource, scope after resolution).

**Recommendation for 10.5: (a).** 10.1 only names the conflict and supplies the `LocationResource` type that (a) needs.

### 13.3 Typed resources (new module; 10.1D)

```
LocationResource(corridor_id, section_id | None)      # REPORT_JOB after resolution
JobResource(job_id, corridor_id, section_id | None,   # single-job actions
            status)
CorridorResource(corridor_id)                          # optimize, optimization run, list gate
```

Frozen dataclasses in a **new** `identity/resource.py`. Scope is resolved **before** authorization by the caller, which already holds the job (or resolved location): the *service resolves the resource; the policy decides*. The policy never re-reads the repository (which also keeps SoD's history access a separate, explicit dependency, parent §12.5).

### 13.4 Growing the seam without breaking 8 test doubles

Passing a third argument breaks every 2-argument policy. Preferred additive design, **all in new modules, `authorization.py` untouched in 10.1**:

```
class ResourceAuthorizationPolicy(Protocol):        # runtime_checkable
    def authorize_resource(self, actor, action, resource) -> None: ...
```

At 10.5 a single helper in the service calls `authorize(actor, action)` **always** (unchanged behavior; all 8 test doubles keep working) and *additionally* calls `authorize_resource(...)` **only if the policy implements it**. `UnenforcedPolicy` needs no change. An enforcing policy implements both (the 2-arg form does the RBAC-only check).

### 13.5 The matcher (10.1D), pure

```
class ScopeMatch(Enum): COVERED, NOT_COVERED, UNRESOLVED, CORRIDOR_MISMATCH, NO_SCOPE

match_scope(scope: RailwayScope | None, resource: LocationResource | JobResource) -> ScopeMatch

class CorridorMatch(Enum): IN_CORRIDOR, CORRIDOR_MISMATCH, NO_SCOPE

match_corridor(scope: RailwayScope | None, resource: CorridorResource) -> CorridorMatch
```

- `scope is None` -> `NO_SCOPE` (ADMIN, or an unassigned actor)
- `resource.section_id is None` -> `UNRESOLVED`
- `resource.corridor_id != scope.corridor_id` -> `CORRIDOR_MISMATCH`
- otherwise exact membership -> `COVERED` / `NOT_COVERED`

`match_scope` is typed for **section-bearing resources only**; passing a `CorridorResource` raises `TypeError` rather than reading a non-existent `section_id`. Corridor-shaped resources (`optimize_corridor`, run reads, the obligation-list gate) use `match_corridor`, which compares `corridor_id` only. `IN_CORRIDOR` means *"the assignment lives in this corridor"*, **not** permission to act on every section; whether a section-scoped role may run corridor-wide actions is 10.5 policy.

**Multi-valued on purpose.** A `bool` return invites `if not match_scope(...)` bugs that mis-handle `None`. Only `COVERED` may ever be treated as permitting; the enum makes the other four impossible to read as "yes".

### 13.6 Where scope comes from at decision time

The enforcing policy (10.5) resolves scope by **`directory.assignment_for(actor.actor_id, actor.role)`**, not from a field on `Actor` (finding #1). Because `(person, role)` is unique and `actor_id` is role-neutral and stable, this returns exactly one assignment. A `DECLARED_UNVERIFIED` actor is only resolved against a `DEMO_DECLARED` directory (§14); an `AUTHENTICATED` actor only against a `DIRECTORY`-basis directory.

---

## 14. Demo compatibility

Existing demo behavior is unchanged: 10.1 touches no request path, and `UnenforcedPolicy` remains the default.

```
DEMO:        DECLARED_UNVERIFIED  +  declared role   +  scope from a server-side DEMO fixture
PRODUCTION:  AUTHENTICATED        +  directory role  +  directory scope
```

Kept un-collapsed by construction:

| Rule | Mechanism |
|---|---|
| A demo actor never becomes `AUTHENTICATED` | 10.1 returns, stores and exposes **no** `Actor` (the only construction is the discarded id-validation probe, §3.3). `AUTHENTICATED` is 10.3, and only from a validated assertion. |
| Demo scope is never client-supplied | Demo scope comes from a **server-side** `DEMO_DECLARED` fixture keyed by the declared `actor_id`. There is no `X-Actor-Section`; the request never carries scope. |
| The declared role cannot upgrade | A demo lookup matches only if the fixture's role equals the declared role; a caller cannot inherit a higher fixture role. |
| The two are distinguishable forever | `AssignmentBasis` on every assignment; `actor_assurance` already `NOT NULL` on every event (`history.py:71`). |
| Trusted resolution ignores demo data | A `DIRECTORY`-basis lookup never consults the demo fixture and vice versa; mixing is a test-enforced error. |
| Demo mode is deployment configuration | Never a request property (parent §17.3). |

**Do demos need scopes at all?** Not for 10.1. With `UnenforcedPolicy`, scope is unread. A demo fixture is only needed if the demo is to *show* enforcement, which is 10.5. The default is: no demo fixture until then.

**Script the demo with distinct actor ids per phase** (parent OQ-7 recommendation); it costs nothing and keeps SoD compatible.

---

## 15. Future NovaForge mapping

```
NovaForge subject  (iss -> identity_provider alias, sub)
  -> Pashupatastra Person (person_id, role-neutral, 1:1)
     -> RoleAssignment(s)  (person_id, role, scope)     [Pashupatastra-owned]
        -> select_active_assignment(...)
           -> RailwayScope
              -> Actor(person_id, role, AUTHENTICATED)   [10.3 only]
```

**Should NovaForge claims contain railway section ids? No.**

1. NovaForge is a generic, single-tenant approval-governance system; it "knows nothing about railway sections" (parent §3.5) and cannot validate a section id against `SectionRegistry`.
2. Assertions are 300-second bearer artifacts (`identityAssertion.ts:13`). Baking scope into them freezes a possibly-stale grant into a token and makes every scope change a re-issue problem.
3. It would make NovaForge the **authority** for railway authorization, violating R-7/R-8 (railway rules and SoD stay in Pashupatastra).
4. It creates a cross-domain escalation path: whoever administers NovaForge users (its `ADMIN`) could mint railway authority. Parent §8.2's "NovaForge ADMIN != railway ADMIN" would be defeated by a different route.
5. Pashupatastra would have to *trust* claims it cannot verify semantically, weakening "no mapping => no access".

The directory is the only place a railway scope may originate. The NovaForge `role` claim is recorded only.

---

## 16. Security red lines

Each maps to an enforcement point or test in 10.1.

| Red line | How 10.1 holds it |
|---|---|
| Client-supplied railway role becoming trusted | `select_active_assignment` returns a directory-held role only; `requested_role` is a selector (§7.3) |
| Client-supplied section becoming trusted scope | Scope has no request input. `RailwayScope` is built only from the directory/fixture and registry-validated |
| NovaForge ADMIN becoming railway ADMIN | Loader rejects any NovaForge-role field; `role` is never an input to the mapping (§11.3) |
| One person gaining global scope accidentally | No wildcard; corridor expanded to explicit ids at authoring; unknown ids refuse load; `NOT_COVERED` for anything outside the set |
| Missing `section_id` becoming global access | `UNRESOLVED` is a distinct, non-permitting result; tested against a job lacking the key (the live `jobs.db` row shape) |
| Authentication failure becoming `DECLARED_UNVERIFIED` | 10.1 yields no `Actor` and has no auth path. `AssignmentBasis` prevents a demo lookup from satisfying a trusted one. The 10.3 rule is recorded as an acceptance criterion there |
| Duplicate identity databases | No table, no accounts, no credentials, no email/name fields; unknown-field rejection |
| Raw authentication tokens in Pashupatastra | The identity model has no token field; a test asserts none exists on any 10.1 type |
| Role assignment based only on display name/email | Key is `(provider, subject)`; there is no name/email attribute to key on |
| One human, two person ids (SoD dodge) | Bijection enforced at load |
| ADMIN gaining operational scope | `scope is None` invariant for ADMIN |

---

## 17. Test strategy (design only; no tests written)

New files, all under `backend/tests/`, none needing the DB:

**`test_slice10_1_person_identity.py`**
- distinct `(provider, subject)` -> distinct persons; duplicate `(provider, subject)` rejected; duplicate `person_id` rejected (**bijection**)
- same `subject` under a different provider is a different person
- `person_id` accepted/rejected exactly as `human_actor` does (empty, too long, leading punctuation, `SYSTEM*`)
- a person keeps one `person_id` across roles
- no field named `email`, `name`, `token`, or provider-role on any identity type; unknown keys rejected by the loader

**`test_slice10_1_scope.py`**
- scope requires a known corridor and known sections (`SectionRegistry.has`); an unknown section refuses construction
- section set is non-empty and a `frozenset`; sections from a different corridor rejected
- **corridor vs section**: the corridor-expansion helper yields explicit ids; a section added to the registry *after* assignment is **not** covered
- **cross-track**: scope is unaffected by track; a job on UP-1 vs DOWN-1 in the same section matches identically (both serve all 6 sections)
- explicit ids copied, not aliased (mutating the source set cannot widen scope)

**`test_slice10_1_role_assignments.py`**
- roles limited to `HUMAN_ROLES`; `SYSTEM` and `UNIDENTIFIED` rejected
- operational roles require scope; ADMIN requires `None`
- `(person, role)` uniqueness
- **multi-role**: one person, ENGINEER + AUTHORITY, different scopes, both retrievable
- **role-specific scopes**: AUTHORITY sees only its own scope, ENGINEER only its own
- **multiple sections** on one assignment
- **one active role per request**: absent selector + single assignment -> it; absent + multiple -> `ROLE_SELECTION_REQUIRED`; absent + none -> `NO_ASSIGNMENT`; selector not held -> `ROLE_NOT_ASSIGNED`; selector `SYSTEM`/`UNIDENTIFIED`/garbage -> `INVALID_ROLE`; never "highest"/"first"
- selector is not a claim: requesting a role never returns one the person does not hold
- results and directory snapshot are immutable (frozen; mutation attempts fail)
- **loader**: every load-time violation in §11.3 refuses to start; missing production file -> empty directory
- NovaForge-role-like input has no effect (a fixture containing `role: SUPER_ADMIN` from a provider is rejected, not ignored)

**`test_slice10_1_resource_scope_match.py`**
- `COVERED` / `NOT_COVERED` / `UNRESOLVED` / `CORRIDOR_MISMATCH` / `NO_SCOPE`
- **missing scope**: `None` -> `NO_SCOPE`; ADMIN never `COVERED`
- **section absent**: a job dict shaped like the live legacy row (`block_candidate` without `section_id`) -> `UNRESOLVED`, never covered
- a resource built from an existing job in each status (`reported`..`completed`) carries the block's section
- three resource shapes are distinct types; `match_scope(..., CorridorResource)` raises `TypeError`; `match_corridor` matches on `corridor_id` only and returns `NO_SCOPE` for ADMIN/unassigned
- on a **one-section** corridor (the `CORRIDOR_A` default) `NOT_COVERED` is unreachable; the `NOT_COVERED` cases run on the 6-section dataset registry
- only `COVERED` is a permit, asserted explicitly

**Demo / authenticated preparation**
- demo lookup keyed by declared id; declared role must equal fixture role; cannot upgrade
- a `DIRECTORY` directory never resolves through demo data and vice versa
- **no 10.1 function returns, stores or exposes an `Actor`**: no public signature or dataclass field is annotated with or holds an `Actor`, and `Actor` is referenced only inside `_validate_person_id` (import/AST-level assertion). Construct-then-discard for id validation is permitted and is the single allowed use
- `IdentityAssurance` membership unchanged; `Actor` field set unchanged; `Actor.to_dict()` keys unchanged (guards finding #1)

**SoD preparation**
- one person, two roles -> same `person_id` (comparison key is role-independent)
- two distinct persons -> distinct ids
- (rule itself: 10.6) the comparator treats `UNIDENTIFIED` and `DECLARED_UNVERIFIED` sides as not comparable

**Stale assignment (only if/when temporal validity is added, not in 10.1):** expired assignment rejected at request time; event-time vs request-time semantics pinned.

**Boundary / regression**
- contract-freeze suite and the existing 8 test-double policies pass **unmodified**
- static check: new modules import no `sqlite3`, no `fastapi`, and none of `jobs.repository`/`service`/`history`

---

## 18. Exact implementation plan

All four steps are **new files only**, plus optional re-exports in `identity/__init__.py`. No edits to `actor.py`, `authorization.py`, `deps.py`, routers, models, schemas, OpenAPI, `jobs.db`, or any existing test.

### 10.1A — Person & external identity key
| | |
|---|---|
| Files | new `backend/app/identity/person.py`; new `backend/tests/test_slice10_1_person_identity.py` |
| New types | `ExternalIdentity(identity_provider, subject)`, `Person(person_id, identity)`, `PersonError` |
| Modified | none |
| DB / API | none / none |
| Invariants | non-empty bounded provider and subject; `person_id` valid per `human_actor` (construct-and-discard inside `_validate_person_id`; result never returned); no role encoded; no email/name/token fields |
| Acceptance | §17 person tests green; bijection enforceable by the directory in 10.1C |

### 10.1B — Railway scope
| | |
|---|---|
| Files | new `backend/app/identity/scope.py`; new `test_slice10_1_scope.py` |
| New types | `RailwayScope(corridor_id, section_ids)`, `ScopeError`, `scope_for_corridor(registry)` (authoring helper: expands to explicit ids) |
| Modified | none. Reads `SectionRegistry` only. **The registry is injected by the caller** (a constructor/loader argument); the identity modules never build or fetch one. The only builder of the default registry is `JobService._build_default_corridor_and_registry()`, which they must not import (that would breach the no-`jobs.service` acceptance check) |
| DB / API | none / none |
| Invariants | non-empty for operational roles; every id validated by `SectionRegistry.has`; corridor matches registry; frozen; no wildcard |
| Acceptance | §17 scope tests green; a later-added section is not covered |

### 10.1C — Role assignment, directory, active-role selection
| | |
|---|---|
| Files | new `backend/app/identity/assignments.py`; new `test_slice10_1_role_assignments.py`; optional checked-in **demo** fixture, clearly labelled |
| New types | `AssignmentBasis`, `RoleAssignment`, `ActiveAssignment`, `AssignmentDirectory` (Protocol), `StaticAssignmentDirectory`, `select_active_assignment`, `AssignmentError` with reason codes (`INVALID_ROLE`, `ROLE_NOT_ASSIGNED`, `NO_ASSIGNMENT`, `ROLE_SELECTION_REQUIRED`) |
| Modified | none |
| DB / API | none / none |
| Invariants | `(person, role)` unique; `(provider, subject)` and `person_id` unique; ADMIN scope `None`; roles in `HUMAN_ROLES`; unknown fields rejected; **no function returns or exposes an `Actor`** |
| Acceptance | every §11.3 load violation refused; selection table (§7.2) exhaustively tested; immutability tested |

### 10.1D — Resource types and scope matching
| | |
|---|---|
| Files | new `backend/app/identity/resource.py`; new `backend/tests/test_slice10_1_resource_scope_match.py` |
| New types | `LocationResource`, `JobResource`, `CorridorResource`, `ScopeMatch`, `CorridorMatch`, `match_scope`, `match_corridor`, `resource_from_job(job_dict, corridor_id)` (reads `block_candidate.section_id` defensively, returns `None` section when absent), `ResourceAuthorizationPolicy` Protocol |
| Modified | none. **`authorization.py` untouched** |
| DB / API | none / none |
| Invariants | multi-valued match; absent section -> `UNRESOLVED`; corridor mismatch -> denied class; resources are frozen |
| Acceptance | legacy-shaped job yields `UNRESOLVED`; existing 8 two-arg policies and the contract-freeze suite pass unmodified |

**Gate after 10.1D:** `git diff --stat` must show only additions of the new files (+ optional `identity/__init__.py` re-exports); a full test run must pass with zero modified existing tests.

Order is by dependency (A -> B -> C -> D), each independently testable and mergeable.

---

## 19. Deferred work / rejected scope

**Explicitly rejected for 10.1** (each belongs elsewhere or later):

| Item | Why not now |
|---|---|
| Full user management, password handling, login UI | NovaForge's job; duplicate identity system (R-1) |
| OAuth / front-channel hop | Parent OQ-1; NovaForge change; 10.7 |
| Any NovaForge modification | Forbidden; and unnecessary for 10.1 |
| Assertion verification, JWKS | 10.2 |
| `AUTHENTICATED` assurance, `request_actor` change | 10.3 (three tests pin the enum; not touched here) |
| Audit metadata (`sub`, `iss`, `sid`, scope) | 10.4 |
| Enforcing policy, call-site enrichment, new 403 codes | 10.5 |
| Segregation-of-duties rule | 10.6 (needs event-history access) |
| Notification recipients | No user directory; red line R-9 |
| Frontend | R-11 |
| Generic policy engine | Exact-set matching is enough; no evidence for more |
| Organization hierarchy, HR system, directory sync | No evidence; over-build |
| Temporal validity, assignment versioning, admin surface | §8.3, §9; parent D-2 |
| Crew / division / jurisdiction | No concept (OQ-3, D-6) |
| Track- or asset-level scope | Track discriminates nothing; asset has no section field |
| SQL scope filtering of lists | No section column; separate problem |
| Splitting `COMMIT_BLOCK` into approve vs commit | Parent D-9 |

---

## 20. Acceptance criteria (Slice 10.1 complete when all hold)

1. `git diff` against `985ca04` contains **only new files**, plus optional re-exports in `identity/__init__.py`.
2. **No** change to `actor.py` (so `Actor`, `ActorRole`, `IdentityAssurance`, `Actor.to_dict()` are byte-identical) and none to `authorization.py`, `deps.py`, routers, `jobs/models.py`, `contracts/`, OpenAPI, schemas.
3. No `sqlite3` import in the new modules; no new table; `jobs.db` untouched.
4. All existing tests pass **unmodified**, including `test_actor_identity.py::test_no_authenticated_assurance_exists_yet`, `test_slice7_contract_freeze.py::test_no_authenticated_assurance_exists`, `test_slice9_safety.py::test_no_authenticated_assurance_level_was_introduced`, the contract-freeze suite, and all 8 two-argument test policies.
5. All §17 tests exist and pass.
6. **No function in the 10.1 modules returns, stores or exposes an `Actor`.** The single permitted use is the construct-and-discard id-validation probe inside `_validate_person_id` (§3.3); an AST test pins that `Actor` appears nowhere else.
7. `person_id` bijection and role-neutrality are enforced at load.
8. Scope is explicit-set only; no wildcard; unknown sections refuse load.
9. Absent `section_id` is `UNRESOLVED` and never permitting.
10. ADMIN cannot carry scope; `SYSTEM`/`UNIDENTIFIED` cannot be assigned.
11. Nothing in the new types holds a token, email, name, or NovaForge role.

---

## 21. Open questions (each with a safe default; none blocks 10.1)

| # | Question | Default adopted | Binds |
|---|---|---|---|
| Q1 | Is scope corridor-qualified (`corridor_id` + sections) or a bare section set? | Corridor-qualified (cheap; prevents future cross-corridor id collision) | 10.1B. Reversible: the corridor field can be dropped without data migration in a config-seeded directory |
| Q2 | Selector input for the active role (header/param name and transport) | Undecided | **10.3** (it arrives with the authenticated request path). 10.1 only defines the selection function |
| Q3 | Derive the active role from `(action, resource)` instead of a selector? | Selector only in 10.1 | 10.5 (needs the action->roles policy table) |
| Q4 | Cross-section asset association: job section only, or both sections? | Job/block section; use `require_same_section=True` in enforcing deployments (§6.4) | 10.5, needs a domain-safety ruling; **does not block 10.1** |
| Q5 | Who authors and approves the production assignment file, and how is the first ADMIN bootstrapped? | Deployment-supplied, reviewed via version control | Parent OQ-8; before any production use, not before 10.1 |
| Q6 | May ADMIN read job history / obligations? | Data model makes ADMIN unscoped; access rule undecided | 10.5 |
| Q7 | `REPORT_JOB` ordering: authorize after resolution, or twice? | After the pure resolution step | 10.5 (§13.2) |
| Q8 | Legacy job rows lacking a section: backfill vs permanently `UNRESOLVED` (denied to scoped roles)? | Permanently `UNRESOLVED`; **no** backfill or migration in this program | 10.5; a data decision on 1 demo row |
| Q9 | Person id allocation scheme (`P-000123`) and who allocates | Opaque, allocated by the directory author | 10.1C fixture authoring |
| Q10 | Section-scoped enforcement can only be shown on a multi-section corridor | Demonstrate/test enforcement on `CORR-NDLS-AGC` (`PASHUPAT_CORRIDOR_ID`); on default `CORRIDOR_A` (1 section) scope is trivially the whole corridor | 10.5 / demo scripting (§5.1) |

---

## 22. Verdict

# GREEN

Test applied: *does any part of this slice require an approval that cannot be made from repository evidence?* **No.**

- The person key, role set, scope shape, assignment cardinality, selection semantics, DB decision and API impact are each determined by cited code.
- The remaining questions (Q2-Q8) are either deferred to 10.3/10.5/10.6 or carry a reversible default with no data or contract cost.
- The parent's AMBER driver, **OQ-1 (the NovaForge front-channel hop)**, is not in this slice's path: 10.1 touches neither NovaForge nor any authentication flow.

**Conditions the implementation must respect** (guardrails, not approvals):

1. Scope goes **beside** the `Actor`, never inside it (finding #1).
2. `person_id` must be role-neutral and bijective with the external identity (finding #7).
3. The seam grows by an **optional second method**, not a wider signature, or 8 test doubles break (finding #6).
4. **Nothing in 10.1 may call the directory from a request path.** It becomes live only in 10.3/10.5; before then it is verified by tests alone.

**Risk to name honestly:** 10.1 ships with no production consumer, so it can rot or be over-designed. The mitigation is the acceptance criteria above (no existing file edited, exhaustive invariant tests) and keeping 10.3 as the slice that consumes it.

**Exact next implementation step:** **10.1A** — create `backend/app/identity/person.py` (`ExternalIdentity`, `Person`, validation reusing `human_actor` for the id rule) with `backend/tests/test_slice10_1_person_identity.py`, writing the tests first. No existing file is edited.

---

## Appendix A — Read-only evidence

| Check | Method | Result |
|---|---|---|
| `jobs.db` integrity | `sha256sum jobs.db` before, during and after every probe | `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386` (unchanged) |
| `jobs.db` contents | `sqlite3.connect("file:...jobs.db?mode=ro&immutable=1", uri=True)` | tables: `maintenance_jobs`, `optimization_runs`, `job_events`, `sqlite_sequence`; **1 job, whose `block_candidate_json` has no `section_id` key**; `job_events` empty |
| Section registry | `load_corridor_dataset()` with `PASHUPAT_JOBS_DB` set to a non-existent temp path | 6 sections, all served by `UP-1` and `DOWN-1`; probe created no DB file |
| Default corridor | `JobService._build_default_corridor_and_registry()` with `PASHUPAT_JOBS_DB` at a non-existent temp path | `CORRIDOR_A`: **1 section** (`CORRIDOR_A_S0-CORRIDOR_A_S1`), tracks `UP-1`,`DOWN-1`; no DB file created |
| `authorize()` call sites | `grep -rn '\.authorize(' backend/app` | 17 matches = 16 calls + 1 docstring (`service.py:479`) |
| 2-arg policy doubles | `grep -rn 'def authorize' backend` | 8 in tests (6 files) + 2 in `authorization.py` |
| NovaForge (read-only) | `schema.prisma:54-66`, `identityAssertion.ts:16` | `User.id` uuid; issuer defaults to `rpOrigin`. NovaForge working tree already had ` M server/prisma/migrations/migration_lock.toml` before this audit; **not touched** |

## Appendix B — Files read

`backend/app/identity/{actor,authorization,__init__}.py`, `backend/app/api/deps.py`, `backend/app/data/{models,section_registry,corridor_dataset}.py`, `backend/app/jobs/{models,service,lifecycle,events,history,repository,asset_association,resource_resolution,proposal,execution,obligations,optimization}.py`, `backend/app/audit/models.py`, `contracts/{schemas,common}.py`, `backend/tests/{conftest,test_job_lifecycle_accountability}.py` and the other test files named above, `docs/SLICE10_IDENTITY_AUTHORIZATION_ARCHITECTURE.md`, plus NovaForge `schema.prisma` and `identityAssertion.ts` (read-only).
