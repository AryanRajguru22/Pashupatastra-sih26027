"use client";

import { useState } from "react";
import {
  ApiError,
  api,
  type Evidence,
  type Execution,
  type Job,
} from "@/lib/api";
import {
  observed,
  planMinuteToIso,
  planWindow,
  recorded,
  shortId,
} from "@/lib/time";
import { useSession } from "@/lib/session";
import { pick } from "@/lib/railwayData";
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_WARN,
  Chip,
  DemoModeNote,
  ErrorBox,
  INPUT,
  Kv,
  LABEL,
  Modal,
  Spinner,
  TimeTag,
  useToast,
} from "@/components/ui";

const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)$/;
const KINDS = ["PHOTO", "VIDEO", "DOCUMENT", "MEASUREMENT"] as const;

interface EvItem {
  ref: string;
  kind: Evidence["evidence_kind"];
  capturedAt: string; // "" = default (actual start)
  lat: string;
  lon: string;
  note: string;
}

const blankItem = (): EvItem => ({
  ref: "",
  kind: "PHOTO",
  capturedAt: "",
  lat: "",
  lon: "",
  note: "",
});

function toEvidence(items: EvItem[], defaultAt: string): Evidence[] {
  return items.map((i) => ({
    evidence_reference: i.ref.trim(),
    evidence_kind: i.kind,
    captured_at: i.capturedAt.trim() || defaultAt,
    ...(i.lat.trim() !== "" && i.lon.trim() !== ""
      ? { latitude: Number(i.lat), longitude: Number(i.lon) }
      : {}),
    ...(i.note.trim() ? { note: i.note.trim() } : {}),
  }));
}

function validateItems(
  items: EvItem[],
  min: number,
  forbidRefs: string[],
): string | null {
  if (items.length < min) return `Add at least ${min} evidence reference.`;
  if (items.length > 10) return "At most 10 evidence items are allowed.";
  const seen = new Set<string>();
  for (const [n, i] of items.entries()) {
    const r = i.ref.trim();
    if (!r) return `Evidence item ${n + 1}: reference is required.`;
    if (r.length > 200) return `Evidence item ${n + 1}: reference exceeds 200 characters.`;
    if (seen.has(r)) return `Evidence item ${n + 1}: reference repeats within this set.`;
    seen.add(r);
    if (forbidRefs.includes(r))
      return `Evidence item ${n + 1}: a before-work reference must not be reused as after-work evidence.`;
    if (i.capturedAt.trim() && !ISO_RE.test(i.capturedAt.trim()))
      return `Evidence item ${n + 1}: captured time needs an explicit offset, e.g. 2026-09-11T00:17:00+05:30.`;
    if ((i.lat.trim() === "") !== (i.lon.trim() === ""))
      return `Evidence item ${n + 1}: give both latitude and longitude, or neither.`;
    if (i.lat.trim() !== "") {
      const la = Number(i.lat);
      const lo = Number(i.lon);
      if (!(la >= -90 && la <= 90 && lo >= -180 && lo <= 180))
        return `Evidence item ${n + 1}: coordinates out of range.`;
    }
    if (i.note.length > 500) return `Evidence item ${n + 1}: note exceeds 500 characters.`;
  }
  return null;
}

function EvidenceEditor({
  label,
  items,
  setItems,
  placeholder,
  max = 10,
}: {
  label: string;
  items: EvItem[];
  setItems: (v: EvItem[]) => void;
  placeholder: string;
  max?: number;
}) {
  const upd = (idx: number, patch: Partial<EvItem>) =>
    setItems(items.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  return (
    <div className="flex flex-col gap-space-sm">
      <div className="flex items-center justify-between">
        <span className={LABEL}>{label}</span>
        <button
          type="button"
          className={BTN_GHOST}
          disabled={items.length >= max}
          onClick={() => setItems([...items, blankItem()])}
        >
          <span className="material-symbols-outlined text-[14px]">add</span>
          Add evidence item
        </button>
      </div>
      {items.map((it, idx) => (
        <div
          key={idx}
          className="rounded-DEFAULT bg-surface-container-lowest p-space-sm flex flex-col gap-space-xs"
        >
          <div className="grid grid-cols-12 gap-space-xs">
            <input
              aria-label={`Evidence reference ${idx + 1}`}
              className={`${INPUT} col-span-8`}
              placeholder={placeholder}
              value={it.ref}
              maxLength={200}
              onChange={(e) => upd(idx, { ref: e.target.value })}
            />
            <select
              aria-label={`Evidence kind ${idx + 1}`}
              className={`${INPUT} col-span-3`}
              value={it.kind}
              onChange={(e) =>
                upd(idx, { kind: e.target.value as EvItem["kind"] })
              }
            >
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <button
              type="button"
              aria-label={`Remove evidence item ${idx + 1}`}
              className="col-span-1 text-outline hover:text-error"
              onClick={() => setItems(items.filter((_, i) => i !== idx))}
            >
              <span className="material-symbols-outlined">delete</span>
            </button>
          </div>
          <div className="grid grid-cols-12 gap-space-xs">
            <input
              aria-label={`Captured at ${idx + 1}`}
              className={`${INPUT} col-span-6`}
              placeholder="captured at (default: actual start) e.g. 2026-09-11T00:17:00+05:30"
              value={it.capturedAt}
              onChange={(e) => upd(idx, { capturedAt: e.target.value })}
            />
            <input
              aria-label={`Latitude ${idx + 1}`}
              className={`${INPUT} col-span-2`}
              placeholder="lat"
              inputMode="decimal"
              value={it.lat}
              onChange={(e) => upd(idx, { lat: e.target.value })}
            />
            <input
              aria-label={`Longitude ${idx + 1}`}
              className={`${INPUT} col-span-2`}
              placeholder="lon"
              inputMode="decimal"
              value={it.lon}
              onChange={(e) => upd(idx, { lon: e.target.value })}
            />
            <input
              aria-label={`Note ${idx + 1}`}
              className={`${INPUT} col-span-2`}
              placeholder="note"
              value={it.note}
              onChange={(e) => upd(idx, { note: e.target.value })}
            />
          </div>
        </div>
      ))}
      <p className="font-label-mono text-[11px] text-outline">
        Evidence is recorded as a reference (ID or path in another system)
        with kind and capture time. This demo does not upload, store or open
        files. Coordinates are range-checked only; no geofencing.
      </p>
    </div>
  );
}

type Mode = "start" | "complete" | "notdone";

export default function ExecutionActions({
  job,
  onDone,
}: {
  job: Job;
  onDone?: () => void;
}) {
  const { persona, online, bump } = useSession();
  const toast = useToast();
  const [mode, setMode] = useState<Mode | null>(null);
  const [exec, setExec] = useState<Execution | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState<Mode | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [localErr, setLocalErr] = useState<string | null>(null);

  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [reason, setReason] = useState("");
  const [items, setItems] = useState<EvItem[]>([blankItem()]);

  const isWorker = persona.role === "WORKER";
  const disabled = !isWorker || online === false;
  const why = !isWorker
    ? "Switch to the Worker persona (demo role gating; not server-enforced)."
    : online === false
      ? "Backend offline"
      : undefined;

  const ps = job.schedule_start_minute;
  const pe = job.schedule_end_minute;

  const open = async (m: Mode) => {
    setError(null);
    setLocalErr(null);
    setReason("");
    setItems(m === "notdone" ? [] : [blankItem()]);
    setLoading(m);
    try {
      if (m === "start") {
        setExec(null);
        setStartAt(ps != null ? planMinuteToIso(ps + 1) : "");
      } else {
        const list = await api.executions(job.job_id);
        const openExec =
          [...list.executions].reverse().find((x) => x.status === "STARTED") ??
          null;
        if (!openExec) {
          throw new ApiError(
            0,
            "NO_OPEN_EXECUTION",
            "No open (STARTED) execution record was found for this job.",
          );
        }
        setExec(openExec);
        setEndAt(pe != null ? planMinuteToIso(pe) : "");
      }
      setMode(m);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e));
      setError(err);
      toast(`${err.code}: ${err.detail}`, "warn");
    } finally {
      setLoading(null);
    }
  };

  const close = () => {
    if (busy) return;
    setMode(null);
    setError(null);
    setLocalErr(null);
  };

  const submit = async () => {
    setLocalErr(null);
    setError(null);
    if (!mode) return;
    try {
      let res: { job: Job; execution: Execution; message: string };
      if (mode === "start") {
        if (!ISO_RE.test(startAt.trim()))
          return setLocalErr("Actual start needs an explicit offset, e.g. 2026-09-11T00:17:00+05:30.");
        const bad = validateItems(items, 1, []);
        if (bad) return setLocalErr(bad);
        const startMs = Date.parse(startAt.trim());
        for (const i of items) {
          const c = Date.parse(i.capturedAt.trim() || startAt.trim());
          if (c > startMs)
            return setLocalErr("Before-work evidence must be captured at or before the actual start.");
        }
        if (!job.proposal_run_id) return setLocalErr("Job has no proposal run id.");
        setBusy(true);
        res = await api.startExecution(
          persona,
          job.job_id,
          job.proposal_run_id,
          startAt.trim(),
          toEvidence(items, startAt.trim()),
        );
      } else {
        if (!exec) return;
        if (!ISO_RE.test(endAt.trim()))
          return setLocalErr("Actual end needs an explicit offset, e.g. 2026-09-11T01:46:00+05:30.");
        if (Date.parse(endAt.trim()) < Date.parse(exec.actual_start_at))
          return setLocalErr("Actual end cannot be before the actual start.");
        if (mode === "complete") {
          const before = exec.before_work_evidence.map((x) => x.evidence_reference);
          const bad = validateItems(items, 1, before);
          if (bad) return setLocalErr(bad);
          for (const i of items) {
            const c = Date.parse(i.capturedAt.trim() || endAt.trim());
            if (c < Date.parse(exec.actual_start_at))
              return setLocalErr("After-work evidence must be captured at or after the actual start.");
          }
          setBusy(true);
          res = await api.completeExecution(
            persona,
            job.job_id,
            exec.execution_id,
            endAt.trim(),
            toEvidence(items, endAt.trim()),
          );
        } else {
          if (!reason.trim()) return setLocalErr("A reason is required.");
          const bad = items.length ? validateItems(items, 0, []) : null;
          if (bad) return setLocalErr(bad);
          setBusy(true);
          res = await api.notCompleted(
            persona,
            job.job_id,
            exec.execution_id,
            endAt.trim(),
            reason.trim(),
            toEvidence(items, endAt.trim()),
          );
        }
      }
      toast(`${res.message} (${res.job.job_id} → ${res.job.status}, ${res.execution.execution_id}).`);
      setMode(null);
      bump();
      onDone?.();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)));
      bump();
    } finally {
      setBusy(false);
    }
  };

  const title =
    mode === "start"
      ? "Start execution"
      : mode === "complete"
        ? "Complete execution"
        : "Report not completed";

  return (
    <>
      <div className="flex flex-wrap items-center gap-space-sm">
        {job.status === "notified" && (
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={disabled || loading !== null}
            title={why}
            onClick={() => open("start")}
          >
            {loading === "start" && <Spinner />}
            <span className="material-symbols-outlined text-[16px]">play_arrow</span>
            Start execution
          </button>
        )}
        {job.status === "in_progress" && (
          <>
            <button
              type="button"
              className={BTN_PRIMARY}
              disabled={disabled || loading !== null}
              title={why}
              onClick={() => open("complete")}
            >
              {loading === "complete" && <Spinner />}
              <span className="material-symbols-outlined text-[16px]">check_circle</span>
              Complete execution
            </button>
            <button
              type="button"
              className={BTN_WARN}
              disabled={disabled || loading !== null}
              title={why}
              onClick={() => open("notdone")}
            >
              {loading === "notdone" && <Spinner />}
              Report not completed…
            </button>
          </>
        )}
        {(job.status === "notified" || job.status === "in_progress") && why && (
          <span className="font-label-mono text-[11px] text-outline">{why}</span>
        )}
      </div>
      {error && !mode && <ErrorBox error={error} />}

      <Modal open={mode !== null} title={title} onClose={close} busy={busy} wide>
        <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
          <Kv k="JOB">
            {job.job_id} · {job.track_id}
          </Kv>
          {ps != null && pe != null && (
            <Kv k="PLANNED BLOCK">
              {planWindow(ps, pe)} <TimeTag kind="PLAN" />
            </Kv>
          )}
          <Kv k="PROPOSAL RUN">{shortId(job.proposal_run_id, 12)}</Kv>
          {exec && (
            <>
              <Kv k="EXECUTION">{exec.execution_id}</Kv>
              <Kv k="ACTUAL START">
                {observed(exec.actual_start_at)} <TimeTag kind="OBSERVED" />
              </Kv>
            </>
          )}
          <Kv k="ACTING AS">
            {persona.id} ({persona.role}) — declared, not verified
          </Kv>
        </div>

        {mode === "start" ? (
          <label className="flex flex-col gap-space-2xs">
            <span className={LABEL}>Actual start (entered by crew, explicit offset) *</span>
            <input
              className={INPUT}
              value={startAt}
              onChange={(e) => setStartAt(e.target.value)}
              placeholder="2026-09-11T00:17:00+05:30"
            />
            <span className="font-label-mono text-[11px] text-outline">
              Default is the planned start + 1 minute in plan time. The demo
              plan runs on a fixed horizon (10–11 Sep 2026){pick("", ", synthetic")}; the
              server records its own recording time separately.
            </span>
          </label>
        ) : (
          <label className="flex flex-col gap-space-2xs">
            <span className={LABEL}>Actual end (entered by crew, explicit offset) *</span>
            <input
              className={INPUT}
              value={endAt}
              onChange={(e) => setEndAt(e.target.value)}
              placeholder="2026-09-11T01:46:00+05:30"
            />
          </label>
        )}

        {mode === "notdone" && (
          <label className="flex flex-col gap-space-2xs">
            <span className={LABEL}>Reason *</span>
            <textarea
              className={`${INPUT} min-h-20 resize-y`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <span className="font-label-mono text-[11px] text-outline">
              The commitment is released and the job returns to the queue for
              re-planning.
            </span>
          </label>
        )}

        {mode && (
          <EvidenceEditor
            label={
              mode === "start"
                ? "Before-work evidence (1–10) *"
                : mode === "complete"
                  ? "After-work evidence (1–10) *"
                  : "Failure evidence (0–10, optional)"
            }
            items={items}
            setItems={setItems}
            placeholder={
              mode === "start"
                ? `${pick("demo-input", "synthetic-demo")}/execution/before-1.jpg`
                : `${pick("demo-input", "synthetic-demo")}/execution/after-1.jpg`
            }
          />
        )}

        <DemoModeNote />
        {localErr && <ErrorBox error={localErr} title="CHECK THE FORM" />}
        {error && <ErrorBox error={error} />}

        <div className="flex justify-end gap-space-sm">
          <button type="button" className={BTN_GHOST} onClick={close} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={mode === "notdone" ? BTN_WARN : BTN_PRIMARY}
            disabled={busy || online === false}
            onClick={submit}
          >
            {busy && <Spinner />}
            {title}
          </button>
        </div>
      </Modal>
    </>
  );
}

/** Read-only view of one execution record (ExecutionResponse). */
export function ExecutionRecordView({ x }: { x: Execution }) {
  const ev = (list?: { evidence_id: string; evidence_reference: string; evidence_kind: string; captured_at: string; phase: string }[]) =>
    (list ?? []).map((e) => (
      <div
        key={e.evidence_id}
        className="font-label-mono text-[11px] text-on-surface-variant break-all"
      >
        <span className="text-primary-fixed">{e.evidence_id}</span> ·{" "}
        {e.evidence_kind} · {e.evidence_reference} ·{" "}
        {observed(e.captured_at)}
      </div>
    ));
  return (
    <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-label-mono text-label-mono text-primary">
          {x.execution_id}
        </span>
        <Chip tone={x.status === "COMPLETED" ? "cyan" : x.status === "STARTED" ? "violet" : "amber"}>
          {x.status}
        </Chip>
        <span className="font-label-mono text-[11px] text-outline">
          attempt {x.attempt_number}
        </span>
      </div>
      <Kv k="PLANNED (PLAN)">
        {planWindow(x.planned_start_minute, x.planned_end_minute)}
      </Kv>
      <Kv k="ACTUAL START (OBSERVED)">{observed(x.actual_start_at)}</Kv>
      <Kv k="ACTUAL END (OBSERVED)">{observed(x.actual_end_at)}</Kv>
      <Kv k="STARTED BY">
        {x.started_by.actor_id} ({x.started_by.role}, {x.started_by.assurance})
      </Kv>
      <Kv k="START RECORDED">{recorded(x.started_recorded_at)}</Kv>
      {x.ended_by && (
        <Kv k="ENDED BY">
          {x.ended_by.actor_id} ({x.ended_by.role}, {x.ended_by.assurance})
        </Kv>
      )}
      <Kv k="END RECORDED">{recorded(x.ended_recorded_at)}</Kv>
      <Kv k="DEVIATIONS">
        started before planned start:{" "}
        {String(x.deviations.started_before_planned_start)} · ended after
        planned end: {String(x.deviations.ended_after_planned_end ?? "—")}
      </Kv>
      <Kv k="BLOCK DIGEST">
        <span title={x.committed_block_digest}>
          {x.committed_block_digest.slice(0, 16)}…
        </span>
      </Kv>
      {x.not_completed_reason && (
        <Kv k="NOT COMPLETED REASON">{x.not_completed_reason}</Kv>
      )}
      <div className="mt-1">
        <div className="font-label-caps text-label-caps text-outline">
          BEFORE-WORK EVIDENCE (references only)
        </div>
        {ev(x.before_work_evidence)}
        {(x.after_work_evidence?.length ?? 0) > 0 && (
          <>
            <div className="font-label-caps text-label-caps text-outline mt-1">
              AFTER-WORK EVIDENCE
            </div>
            {ev(x.after_work_evidence)}
          </>
        )}
        {(x.failure_evidence?.length ?? 0) > 0 && (
          <>
            <div className="font-label-caps text-label-caps text-outline mt-1">
              FAILURE EVIDENCE
            </div>
            {ev(x.failure_evidence)}
          </>
        )}
      </div>
    </div>
  );
}
