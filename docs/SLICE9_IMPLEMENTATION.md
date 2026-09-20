# Slice 9 — Notifications, SLA, Escalation, Accountability (implementation record)

**Project:** Pashupatastra — SIH26027
**Baseline:** `256b5d43f10841db9aaf30ea9f13ea9d398cc07d`
**Design gate:** `docs/SLICE9_NOTIFICATIONS_SLA_ESCALATION_ARCHITECTURE.md`
**Scope:** backend only. No frontend work.

---

## 1. The ten statements this slice must keep making

Every one of these is enforced by a test, not only by this document.

1. **The SLA values are ASSUMED DEMO ENGINEERING VALUES.** They are not Indian
   Railways policy, are not derived from any IR circular, manual, schedule of powers
   or corridor agreement, and have not been reviewed or approved by any railway
   authority. `SlaPolicy.__post_init__` **refuses to construct** a policy whose
   version does not contain the word `assumed`, and refuses `assumed=False`
   outright — so the claim cannot be quietly deleted.
2. **Obligations are addressed to roles** (`AUTHORITY`, `WORKER`, `ENGINEER`), never
   to individuals. There is no user table, no authority directory, no contact detail
   and no job-to-person assignment. An escalation says *an obligation* is late; it
   can never assert that a named person failed.
3. **Actor identity remains `DECLARED_UNVERIFIED`.** No `AUTHENTICATED` assurance
   level was added.
4. **No authentication and no RBAC exist.** `UnenforcedPolicy.enforcing` is still
   `False`; no permission in this repository is enforced.
5. **No real notification is delivered.** No SMS, email, push, WhatsApp, webhook,
   Twilio, FCM, SMTP, provider or credential exists.
6. **`RecordingChannel` performs no external I/O.** It appends to an in-process list
   and returns `RECORDING_ONLY`. The vocabulary contains no `DELIVERED`, no `READ`
   and no `SENT`.
7. **No scheduler exists.** No cron, worker, task queue or background sweep.
   Obligations are evaluated **on read**.
8. **The execution SLA anchors on `BLOCK_COMMITTED.occurred_at`**, not on the block's
   planned window.
9. **The planned-window SLA is deferred** until the planning horizon tracks real
   time — a data-infrastructure dependency, not a design preference (see §5).
10. **No holiday or working-calendar logic exists**, and no recipient directory
    exists. All SLA arithmetic is wall-clock.

---

## 2. What was built

| # | Item | File |
|---|---|---|
| 9.1 | `SlaPolicy` / `SlaPolicySet` — versioned, frozen, severity-resolved, injectable | `backend/app/jobs/sla_policy.py` (new) |
| 9.2 | `JobObligation` — derived read model, never persisted | `backend/app/jobs/obligations.py` (new) |
| 9.3 | Accountability query service + three read APIs | `service.py`, `router.py`, `models.py`, `history.py`, `repository.py`, `errors.py`, `authorization.py` |
| 9.4 | Notification intent + `NotificationChannel` + `RecordingChannel` | `backend/app/jobs/notifications.py` (new) |
| — | Clock injection finished on every write path | `service.py`, `optimization.py` |
| — | Identified-human guard extended to approve / reject / postpone | `lifecycle.py` |

### Derivation, not storage

`JobObligation` follows the `BlockProposal` / `ExecutionRecord` precedent exactly: it is
a pure function of

```
(job row) + (its append-only history) + (SLA policy) + (one evaluation moment)
```

There is **no** obligation table, **no** `status_entered_at` column, **no** new
`JobStatus`, **no** new `JobEventType` and **no** new lifecycle transition. Two
evaluations at the same moment over the same history are byte-identical, which is what
makes a derived deadline auditable — a stored deadline can silently disagree with the
history it came from; a derived one cannot.

`derive_job_obligation` and `derive_obligations` are pure and take `evaluated_at` as an
argument. **A future sweep or runner calls exactly these functions**, unchanged.

---

## 3. The obligation model

| Obligation | Precondition | Owed by | Clock starts at | Answered by |
|---|---|---|---|---|
| `APPROVAL_PENDING` | `scheduled` + placement event for the **current** run | AUTHORITY | that placement event's `occurred_at` | commit / reject / postpone |
| `EXECUTION_START_PENDING` | `notified` + no start for that run | WORKER | `BLOCK_COMMITTED.occurred_at` | `EXECUTION_STARTED` |
| `COMPLETION_PENDING` | `in_progress` + one open `ExecutionRecord` | WORKER | `ExecutionRecord.started_recorded_at` | `EXECUTION_COMPLETED` / `..._NOT_COMPLETED` |
| `REPLANNING_PENDING` | `reported` after `BLOCK_RELEASED` / `EXECUTION_NOT_COMPLETED`, with no later placement | ENGINEER | the withdrawal event | the next optimization run |
| `NONE` | `completed`, or `reported` awaiting the next run | — | — | — |

**`reported` alone is not an obligation on a human.** A newly reported job is waiting for
an optimizer, not for a person; treating it as an obligation would flood the view with
every report ever filed.

**A rejection or a postponement raises no replanning obligation.** The authority
*answered*; a postponement's chosen date constrains scheduling, not a person.

### Mootness

`MOOT` is produced when the **system** withdrew a proposal (`PROPOSAL_INVALIDATED`,
`OPTIMIZATION_REFUSED`) before an authority answered it. The obligation **ended**; it was
not breached. It reports `is_past_due = False`, `escalation_level = 0` and `due_at = null`
**at every evaluation time, however far in the future**, and it raises no notification
intent. A system withdrawal is never an authority failure, and reporting one as overdue
would be a false accusation — this is the single most safety-critical rule in the slice.

A released commitment (`BLOCK_RELEASED`) or work reported not-completed ends its
obligation the same way, surfacing as `REPLANNING_PENDING` rather than as a breach.

### Attention is orthogonal, not a competing obligation

A `notified` job whose approved block an optimization run could not honour
(`COMMITTED_BLOCK_CONFLICT`) still owes `EXECUTION_START_PENDING` against its own real
deadline **and** needs an engineer to reconcile the conflict. Modelling attention as a
second obligation would force one of those two facts to be dropped, so it is a flag
(`attention_required` + `attention_reason_code` + `attention_event_id`) carried on
whatever obligation the job has. The second attention condition is a recorded integrity
refusal (`TRANSITION_REJECTED` carrying `CommittedStateIntegrityError` /
`ExecutionIntegrityError`) that is still the job's most recent event.

---

## 4. Escalation, and two deliberate deviations from the brief

Escalation is **derived** and **lifecycle-neutral**. It mirrors `ExecutionDeviations` —
*recorded, never enforced*. No deadline, escalation, intent or read ever approves,
rejects, releases, cancels, expires or completes anything. An unanswered proposal stays
`scheduled` and stays committable, however late it is.

### Deviation 1 — `OVERDUE` is unreachable under the shipped policy

The approved values place **L1 at the SLA deadline itself**, while the approved state
ladder is `… → OVERDUE → ESCALATED_L1 → …`. Those two cannot both hold: if L1 begins at
the deadline, nothing is ever "past due but not yet escalated".

**Resolution.** Both were kept, with `OVERDUE` given a precise meaning — *past the
deadline, before the policy's first escalation step*. Under
`ASSUMED_DEMO_SLA_POLICY` (first multiplier `1.0`) it is therefore never produced, and
late obligations report `ESCALATED_L1` upward; a policy configured with a grace period
before L1 does produce it. That is a property of those particular numbers, not of the
derivation.

Because of this, `JobObligation.is_past_due` (true for `OVERDUE` **and** every
`ESCALATED_Ln`) is the predicate for "which are overdue?", and the API exposes
`?past_due=true`. A client filtering `state=OVERDUE` would silently find nothing; this is
documented in `docs/api-contract-v1.md` and pinned by a test.

### Deviation 2 — one added state, `NO_SLA_DEFINED`

`REPLANNING_PENDING` is genuinely owed, but `SlaPolicy` defines exactly three durations
and none of them is a replanning allowance. Reporting it as `WITHIN_SLA` would claim
conformance with a deadline that does not exist, and reporting `NONE` would hide a real
obligation. `NO_SLA_DEFINED` says exactly what is true. It is a derived read-model state,
**not** a lifecycle state; adding a `replanning_sla` field to `SlaPolicy` is the one-line
change that retires it.

---

## 5. Why the execution SLA does not use the planned window

`contracts.DEFAULT_HORIZON_START` is a hard-coded constant (`2026-09-10T00:00:00+05:30`).
Every currently-approved block's planned wall-clock window therefore **already lies in
the past**. A "work should have started at its planned minute" SLA would mark every job
in the system instantly overdue — a deadline that says nothing about anyone's conduct.

Anchoring on `BLOCK_COMMITTED.occurred_at` answers the real question ("an approved block
whose execution has not started within *T* of approval") with zero data dependencies.

The planned-window anchor is the **operationally more meaningful** statement and should
be built — but only once `horizon_start` tracks real time, which requires timetable data
covering the live planning window, because the Slice 4 coverage gate fails closed
otherwise. That is a **data-infrastructure dependency**, and it belongs to a later slice.
The groundwork already exists and was not rebuilt: `ExecutionDeviations` already records
planned-versus-actual, deliberately without enforcing it.

---

## 6. Notifications: intent only

| | Implemented? |
|---|---|
| **Intent** — "role AUTHORITY should be told job J's approval is at L1" | **Yes**, derived. No row. |
| **Attempt** — "at 14:02:11Z a send was attempted via channel C" | No. Not derivable, so durable when it exists. It does not exist. |
| **Result** — "the provider accepted / refused" | No. |

`intent_key = SHA256(canonical_json({job_id, obligation_type, escalation_level,
policy_version, anchor_event_id}))`, built with the project's existing
`backend.app.jobs.events.canonical_json` and hashed exactly as
`execution.block_identity_digest` is — one canonical-JSON spelling and one hashing
convention, not a second that could diverge. `anchor_event_id` is what makes the key
stable across repeated reads and **different after a re-proposal**.

**No outbox**, deliberately. The two canonical outbox failures do not arise when intent
is derived: a crash between commit and send costs *latency*, never an obligation (the
next read recomputes the same intent because the obligation is still true); and a send
that precedes a rolled-back transaction is structurally impossible, because intents are
computed only from events already committed to `job_events`. The residual risk is the
safe direction only — a possible duplicate, never a loss — which is precisely what
`intent_key` lets a future delivery layer de-duplicate against.

**The future boundary.** Real delivery needs exactly **one** new append-only table,
`notification_attempts`, carrying `intent_key` with a UNIQUE constraint (the idiom
`job_events.event_id UNIQUE` already proves), the `policy_version` that justified the
send, and the provider's own result — with `install_append_only_guards` like every other
durable table here. A table for *attempts* is justified because delivery is not
derivable; a table for *intents* never is.

---

## 7. The accountability guard (approve / reject / postpone)

Before this slice, an approval, rejection or postponement could be recorded against
`UNIDENTIFIED` with assurance `NONE`, while the field worker who executed it could not.
The system could say an approval happened, but not always that a **person** made it.

`_require_identified_human` now guards `plan_commit`, `plan_reject` and `plan_postpone`
alongside the four transitions it already guarded.

**The guard is in the lifecycle plan, not in the service method.** `approve_proposal`
delegates to `notify`, and both run `plan_commit`; guarding only the service method would
have left `POST /v1/jobs/{id}/notify` able to record `BLOCK_COMMITTED` for an
unidentified caller — the exact hole the item exists to close.

It is **accountability, not RBAC**: it checks only that *somebody* is named (a HUMAN
actor whose role is not `UNIDENTIFIED`), never *which* role, and the recorded assurance
stays `DECLARED_UNVERIFIED`. `create_job` is deliberately still unguarded — an
unidentified defect report beats a lost one.

**This is a behaviour change on existing v1 operations** for unidentified callers
(`400 INVALID_TRANSITION`). It is not a v1-breaking contract change: no request field
became required, no response status was added to an existing operation, nothing was
renamed, and no response schema's `required` list changed. Seventeen existing tests
depended on unidentified commit succeeding and were updated to the new contract.

---

## 8. Scale assumption (stated, not assumed away)

The cross-job query reads candidate jobs by status in one keyset page, then reads every
one of their histories in **one** `job_events` query
(`JobHistoryRepository.list_for_jobs`), not one per job. Derivation is then O(active
jobs) with a bounded number of events each.

**Measured at demo and pilot scale only** — tens to low thousands of jobs. `job_events`
carries no index on `event_type` or `occurred_at` and **this slice added none**:
indexing is a performance change and belongs with measurements, exactly as Slice 8
deferred its own. A deployment with hundreds of thousands of jobs should revisit it *with
measurements*. `status_entered_at` was explicitly rejected — it would be a second source
of truth for a fact the history already holds.

Pagination is over **candidate jobs**, not over obligations, because obligations are
derived after the page is read and cannot themselves be seeked. A filtered page may
therefore be shorter than `limit` — even empty — while `next_cursor` is still set.

---

## 9. Boundaries held

| Forbidden | Status |
|---|---|
| New `JobStatus` | None. Still the five. |
| New `JobEventType` | None. Still 21. |
| New lifecycle transition | None. `ALLOWED_TRANSITIONS` unchanged. |
| Schema change / migration / `CREATE TABLE` / index | None. No DDL in any Slice 9 module. |
| `status_entered_at` | Not added. |
| Notification / user / recipient table | None. |
| Scheduler, cron, Celery, worker, task queue | None. |
| Real provider or credential | None. |
| Planned-window execution anchor | Deferred (§5). |
| Breaking v1 change | None. Three additive **read** routes; three additive error codes; two additive **read** `JobAction` members. |
| Mutation endpoint for obligations | None, deliberately — acknowledgement without authentication is meaningless, and a silence switch hides operational failure. |

`docs/openapi-v1.json` was regenerated deliberately; the diff against the previous
snapshot **removes zero lines** — it is purely additive. The frozen error-code
vocabulary, the frozen route inventory and the frozen `JobAction` list were each updated
as a deliberate, commented act.
