"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  ApiError,
  CORRIDOR_ID,
  JOB_TYPE_DEFAULT_MIN,
  JOB_TYPE_LABEL,
  PERSONAS,
  api,
  bcMeta,
  scoreBand,
  sectionOf,
  type Job,
  type JobType,
  type Severity,
} from "@/lib/api";
import {
  STATIONS,
  TRACKS,
  adjacentStations,
  convertFieldLocation,
  fmtMetres,
  stationName,
} from "@/lib/fieldLocation";
import { recorded } from "@/lib/time";
import { useSession } from "@/lib/session";
import {
  BTN_GHOST,
  BTN_PRIMARY,
  CARD,
  Chip,
  ErrorBox,
  INPUT,
  Kv,
  LABEL,
  Page,
  PageBanner,
  ProvenanceChip,
  Score,
  Spinner,
  StatusChip,
  useToast,
} from "@/components/ui";

const JOB_TYPES = Object.keys(JOB_TYPE_LABEL) as JobType[];
const SEVERITIES: Severity[] = ["NONE", "MINOR", "MODERATE", "CRITICAL"];

const LOCATION_CODES = new Set([
  "LOCATION_INPUT_CONFLICT",
  "FIELD_LOCATION_INVALID",
  "ASSET_ASSOCIATION_FAILED",
  "INVALID_REQUEST",
]);

const newKey = () =>
  `intake-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;

interface FormState {
  from: string;
  toward: string;
  track: string;
  offsetStart: string;
  offsetEnd: string;
  jobType: JobType | "";
  severity: Severity | "";
  workersMin: number;
  workersMax: number;
  description: string;
  evidence: string;
}

const EMPTY: FormState = {
  from: "MTJ",
  toward: "RKM",
  track: "DOWN-1",
  offsetStart: "",
  offsetEnd: "",
  jobType: "",
  severity: "",
  workersMin: 2,
  workersMax: 4,
  description: "",
  evidence: "",
};

/** DEMO SCRIPT values from the demo runbook (docs handoff §2.1). */
const DEMO_SCRIPT: FormState = {
  from: "MTJ",
  toward: "RKM",
  track: "DOWN-1",
  offsetStart: "5700",
  offsetEnd: "6000",
  jobType: "EMERGENCY_REPAIR",
  severity: "CRITICAL",
  workersMin: 2,
  workersMax: 4,
  description: "Rail fracture found on patrol; caution order issued.",
  evidence: "synthetic-demo/inspection/fracture-mtj-rkm.jpg",
};

export default function InspectionPage() {
  const { persona, setPersonaId, online, bump } = useSession();
  const toast = useToast();
  const [f, setF] = useState<FormState>(EMPTY);
  const [key, setKey] = useState<string>(() => newKey());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [created, setCreated] = useState<Job | null>(null);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setF((cur) => ({ ...cur, [k]: v }));

  const conv = useMemo(
    () =>
      convertFieldLocation(
        f.from,
        f.toward,
        f.offsetStart === "" ? NaN : Number(f.offsetStart),
        f.offsetEnd === "" ? NaN : Number(f.offsetEnd),
      ),
    [f.from, f.toward, f.offsetStart, f.offsetEnd],
  );

  const towardOptions = adjacentStations(f.from);
  const isWorker = persona.role === "WORKER";
  const descOk = f.description.trim().length >= 1 && f.description.length <= 1000;
  const crewOk = f.workersMin >= 1 && f.workersMax >= f.workersMin;
  const evOk = f.evidence.length <= 200;
  const ready =
    conv.ok && f.jobType !== "" && f.severity !== "" && descOk && crewOk && evOk;

  const setFrom = (id: string) => {
    const adj = adjacentStations(id);
    setF((cur) => ({
      ...cur,
      from: id,
      toward: adj.includes(cur.toward) ? cur.toward : (adj[0] ?? ""),
    }));
  };

  const submit = async () => {
    if (!conv.ok || f.jobType === "" || f.severity === "") return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        corridor_id: CORRIDOR_ID,
        track_id: f.track,
        job_type: f.jobType,
        distance_start: conv.distanceStart,
        distance_end: conv.distanceEnd,
        workers_min: f.workersMin,
        workers_max: f.workersMax,
        description: f.description.trim(),
        severity: f.severity,
        ...(f.evidence.trim() ? { evidence_reference: f.evidence.trim() } : {}),
        field_location: {
          from_station_id: f.from,
          toward_station_id: f.toward,
          offset_start_m: Number(f.offsetStart),
          offset_end_m: Number(f.offsetEnd),
        },
        idempotency_key: key,
      };
      const job = await api.createJob(persona, body);
      setCreated(job);
      bump();
      toast(`Job ${job.job_id} created (status: ${job.status}).`);
    } catch (e) {
      setError(
        e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)),
      );
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setCreated(null);
    setError(null);
    setF(EMPTY);
    setKey(newKey());
  };

  const dup = created
    ? (bcMeta(created).duplicate_detection as
        | { possible_duplicate?: boolean; candidate_jobs?: unknown[] }
        | undefined)
    : undefined;
  const locSource = created ? bcMeta(created).location_source : undefined;
  const locationError = error && LOCATION_CODES.has(error.code) ? error : null;
  const otherError = error && !locationError ? error : null;

  return (
    <Page>
      <PageBanner
        eyebrow="FIELD INTAKE // OPERATE"
        title={
          <>
            FIELD INSPECTION{" "}
            <span className="text-outline font-light">{"//"} {CORRIDOR_ID}</span>
          </>
        }
        subtitle="Report a defect at a human-readable location. The report becomes a scored, located maintenance job in the backend."
        right={
          <>
            <ProvenanceChip label="SYNTHETIC TOPOLOGY" />
            <button
              type="button"
              className={BTN_GHOST}
              onClick={() => setF(DEMO_SCRIPT)}
              title="Prefill the scripted live-intake values from the demo runbook"
            >
              <span className="material-symbols-outlined text-[14px] text-primary-fixed">
                history_edu
              </span>
              Load demo script values
              <Chip tone="violet">DEMO SCRIPT</Chip>
            </button>
          </>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-lg items-start">
        {/* LEFT: location */}
        <div className="xl:col-span-6 flex flex-col gap-space-lg">
          <div className={`${CARD} flex flex-col gap-space-lg`}>
            <div className="flex items-center gap-space-xs">
              <span className="material-symbols-outlined text-primary-fixed-dim text-[20px]">
                explore
              </span>
              <h2 className="font-headline-sm text-headline-sm text-on-surface tracking-wide">
                Location — station &amp; offset
              </h2>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-space-md">
              <label className="flex flex-col gap-space-2xs">
                <span className={LABEL}>From station *</span>
                <select
                  className={INPUT}
                  value={f.from}
                  onChange={(e) => setFrom(e.target.value)}
                >
                  {STATIONS.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.id} ({s.name})
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-space-2xs">
                <span className={LABEL}>Toward station * (adjacent only)</span>
                <select
                  className={INPUT}
                  value={f.toward}
                  onChange={(e) => set("toward", e.target.value)}
                >
                  {towardOptions.map((id) => (
                    <option key={id} value={id}>
                      {id} ({stationName(id)})
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="flex flex-col gap-space-xs">
              <span className={LABEL}>Track line *</span>
              <div
                role="radiogroup"
                aria-label="Track line"
                className="grid grid-cols-2 p-1 rounded-DEFAULT bg-surface-container-lowest shadow-inner gap-1"
              >
                {TRACKS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    role="radio"
                    aria-checked={f.track === t}
                    onClick={() => set("track", t)}
                    className={`py-2.5 px-space-md rounded-DEFAULT font-label-mono text-label-mono transition-all ${f.track === t ? "bg-surface-bright text-primary font-bold shadow-[0_0_15px_rgba(0,240,255,0.15)]" : "text-on-surface-variant hover:text-on-surface"}`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-space-md">
              <label className="flex flex-col gap-space-2xs">
                <span className={LABEL}>Offset start (m from {f.from})</span>
                <div className="flex items-center bg-surface-container-lowest rounded-DEFAULT px-space-md py-space-sm shadow-sm border border-outline-variant/30">
                  <input
                    type="number"
                    min={0}
                    step={50}
                    inputMode="numeric"
                    className="bg-transparent text-primary font-label-mono text-label-mono w-full focus:outline-none"
                    value={f.offsetStart}
                    onChange={(e) => set("offsetStart", e.target.value)}
                  />
                  <span className="font-label-mono text-label-mono text-outline">
                    METRES
                  </span>
                </div>
              </label>
              <label className="flex flex-col gap-space-2xs">
                <span className={LABEL}>Offset end (m from {f.from})</span>
                <div className="flex items-center bg-surface-container-lowest rounded-DEFAULT px-space-md py-space-sm shadow-sm border border-outline-variant/30">
                  <input
                    type="number"
                    min={0}
                    step={50}
                    inputMode="numeric"
                    className="bg-transparent text-primary font-label-mono text-label-mono w-full focus:outline-none"
                    value={f.offsetEnd}
                    onChange={(e) => set("offsetEnd", e.target.value)}
                  />
                  <span className="font-label-mono text-label-mono text-outline">
                    METRES
                  </span>
                </div>
              </label>
            </div>

            {/* live chainage preview */}
            <div className="relative overflow-hidden rounded-DEFAULT p-space-md bg-gradient-to-r from-surface-container-high/90 to-surface-container/90 shadow-[0_0_24px_rgba(0,219,233,0.18)]">
              <div className="flex items-start justify-between gap-space-sm mb-space-xs">
                <div className="flex items-center gap-space-2xs">
                  <span className="inline-block w-2.5 h-2.5 rounded-full bg-primary shadow-[0_0_10px_#7df4ff]" />
                  <span className="font-label-caps text-label-caps tracking-widest text-primary-fixed uppercase font-bold">
                    Chainage resolver
                  </span>
                </div>
                <Chip tone={conv.ok ? "cyan" : "amber"}>
                  {conv.ok ? "CROSS-CHECK READY" : "INCOMPLETE"}
                </Chip>
              </div>
              {conv.ok ? (
                <div className="font-label-mono text-label-mono space-y-1">
                  <Kv k="SECTION">
                    <span className="text-primary font-bold">
                      {conv.section.id}
                    </span>{" "}
                    · length {fmtMetres(conv.lengthM)} m
                  </Kv>
                  <Kv k="CORRIDOR CHAINAGE">
                    {fmtMetres(conv.distanceStart)} –{" "}
                    {fmtMetres(conv.distanceEnd)} m (km{" "}
                    {(conv.distanceStart / 1000).toFixed(3)}–
                    {(conv.distanceEnd / 1000).toFixed(3)})
                  </Kv>
                  <Kv k="TRACK">{f.track}</Kv>
                </div>
              ) : (
                <p className="font-label-mono text-label-mono text-secondary">
                  {conv.error}
                </p>
              )}
              <p className="mt-space-sm font-label-mono text-[11px] text-outline">
                Derived client-side from a synthetic section table. The
                backend re-checks it and refuses any mismatch.
              </p>
            </div>

            {/* sector vector diagram: real span position along the section */}
            <div className="flex flex-col gap-space-2xs">
              <span className={LABEL}>Sector diagram</span>
              <div className="relative rounded-DEFAULT overflow-hidden bg-surface-container-lowest p-space-md shadow-inner">
                <div className="absolute inset-0 opacity-20 pointer-events-none bg-[radial-gradient(#00f0ff_1px,transparent_1px)] [background-size:16px_16px]" />
                <div className="relative z-10 flex justify-between font-label-mono text-[11px] text-outline mb-2">
                  <span>{f.from}</span>
                  <span className="text-primary font-semibold">
                    {conv.ok
                      ? `span ${fmtMetres(Number(f.offsetStart))}–${fmtMetres(Number(f.offsetEnd))} m from ${f.from}`
                      : "enter offsets"}
                  </span>
                  <span>{f.toward}</span>
                </div>
                <div className="relative z-10 flex flex-col gap-3">
                  {TRACKS.map((t) => {
                    const active = t === f.track;
                    const l = conv.ok
                      ? (Number(f.offsetStart) / conv.lengthM) * 100
                      : 0;
                    const w = conv.ok
                      ? Math.max(
                          1.2,
                          ((Number(f.offsetEnd) - Number(f.offsetStart)) /
                            conv.lengthM) *
                            100,
                        )
                      : 0;
                    return (
                      <div key={t} className="flex items-center gap-2">
                        <span
                          className={`text-[10px] font-label-mono w-12 ${active ? "text-primary font-bold" : "text-outline"}`}
                        >
                          {t}
                        </span>
                        <div className="relative flex-1 h-1 bg-surface-container-highest rounded">
                          {conv.ok && active && (
                            <div
                              className="absolute h-full bg-error-container shadow-[0_0_12px_#ffb4ab]"
                              style={{ left: `${l}%`, width: `${w}%` }}
                            />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {locationError && (
              <ErrorBox error={locationError} title="LOCATION REFUSED" />
            )}
          </div>
        </div>

        {/* RIGHT: defect + evidence + submit */}
        <div className="xl:col-span-6 flex flex-col gap-space-lg">
          <div className={`${CARD} flex flex-col gap-space-lg`}>
            <div className="flex items-center gap-space-xs">
              <span className="material-symbols-outlined text-secondary text-[20px]">
                warning
              </span>
              <h2 className="font-headline-sm text-headline-sm text-on-surface tracking-wide">
                Defect classification &amp; evidence
              </h2>
            </div>

            <label className="flex flex-col gap-space-2xs">
              <span className={LABEL}>Work type *</span>
              <select
                className={INPUT}
                value={f.jobType}
                onChange={(e) => set("jobType", e.target.value as JobType)}
              >
                <option value="">Select work type…</option>
                {JOB_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {JOB_TYPE_LABEL[t]} — default {JOB_TYPE_DEFAULT_MIN[t]} min
                  </option>
                ))}
              </select>
            </label>

            <div className="flex flex-col gap-space-xs">
              <span className={LABEL}>Reported severity *</span>
              <div
                role="radiogroup"
                aria-label="Severity"
                className="grid grid-cols-2 sm:grid-cols-4 p-1 rounded-DEFAULT bg-surface-container-lowest shadow-inner gap-1"
              >
                {SEVERITIES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={f.severity === s}
                    onClick={() => set("severity", s)}
                    className={`py-2 rounded-DEFAULT font-label-mono text-label-mono transition-all ${f.severity === s ? (s === "CRITICAL" ? "bg-error-container text-error font-bold" : "bg-surface-bright text-primary font-bold") : "text-on-surface-variant hover:text-on-surface"}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-space-md">
              <Stepper
                label="Crew min *"
                value={f.workersMin}
                min={1}
                onChange={(v) =>
                  setF((c) => ({
                    ...c,
                    workersMin: v,
                    workersMax: Math.max(c.workersMax, v),
                  }))
                }
              />
              <Stepper
                label="Crew max *"
                value={f.workersMax}
                min={f.workersMin}
                onChange={(v) => set("workersMax", v)}
              />
            </div>

            <label className="flex flex-col gap-space-2xs">
              <span className={`${LABEL} flex justify-between`}>
                <span>Description *</span>
                <span
                  className={
                    f.description.length > 1000 ? "text-error" : "text-outline"
                  }
                >
                  {f.description.length}/1000
                </span>
              </span>
              <textarea
                className={`${INPUT} min-h-28 resize-y`}
                value={f.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="What was observed?"
              />
            </label>

            <label className="flex flex-col gap-space-2xs">
              <span className={`${LABEL} flex justify-between`}>
                <span>Evidence reference</span>
                <span className="text-outline">{f.evidence.length}/200</span>
              </span>
              <input
                className={INPUT}
                value={f.evidence}
                onChange={(e) => set("evidence", e.target.value)}
                placeholder="ID or path in another system"
              />
              <span className="font-label-mono text-[11px] text-outline">
                Reference only. No file is uploaded or stored.
              </span>
            </label>
          </div>

          {/* submit */}
          <div className={`${CARD} flex flex-col gap-space-md`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-label-mono text-label-mono text-on-surface-variant">
                Reporting as{" "}
                <span className="text-primary">
                  {persona.id} ({persona.role})
                </span>{" "}
                — declared, not verified
              </div>
              <Chip tone="neutral" title="Recorded by the server, not entered here">
                Report time is assigned by the server
              </Chip>
            </div>

            {!isWorker && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-DEFAULT bg-secondary-container/20 px-space-md py-space-sm">
                <span className="font-body-sm text-body-sm text-secondary">
                  Field intake is a Worker action in this demo (role gating is
                  a presentation convenience, not server-enforced).
                </span>
                <button
                  type="button"
                  className={BTN_GHOST}
                  onClick={() => setPersonaId(PERSONAS[0].id)}
                >
                  Switch to {PERSONAS[0].id}
                </button>
              </div>
            )}

            {otherError && <ErrorBox error={otherError} />}
            {error?.code === "IDEMPOTENCY_KEY_CONFLICT" && (
              <div className="flex items-center justify-between gap-2">
                <span className="font-body-sm text-body-sm text-on-surface-variant">
                  This report was already submitted with different values.
                </span>
                <button
                  type="button"
                  className={BTN_GHOST}
                  onClick={() => {
                    setKey(newKey());
                    setError(null);
                  }}
                >
                  Submit as new report
                </button>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-space-sm">
              <button
                type="button"
                className={BTN_PRIMARY}
                disabled={!ready || busy || !isWorker || online === false || !!created}
                onClick={submit}
              >
                {busy ? <Spinner /> : null}
                {busy ? "Submitting…" : "Submit report"}
              </button>
              <button
                type="button"
                className={BTN_GHOST}
                onClick={reset}
                disabled={busy}
              >
                New report
              </button>
              {!ready && (
                <span className="font-label-mono text-[11px] text-outline">
                  Complete location, work type, severity, crew and description.
                </span>
              )}
            </div>
          </div>

          {created && (
            <div
              className={`${CARD} border border-primary-container/40 shadow-[0_0_24px_rgba(0,219,233,0.15)] flex flex-col gap-space-sm`}
            >
              <div className="flex items-center gap-2 font-label-caps text-label-caps text-primary-fixed">
                <span className="material-symbols-outlined text-[18px]">
                  check_circle
                </span>
                JOB CREATED BY BACKEND
              </div>
              <Kv k="JOB ID">
                <Link
                  href={`/jobs/${created.job_id}`}
                  className="text-primary hover:underline"
                >
                  {created.job_id}
                </Link>
              </Kv>
              <Kv k="STATUS">
                <StatusChip status={created.status} />
              </Kv>
              <Kv k="PRIORITY SCORE">
                <Score value={created.priority_score} />{" "}
                <Chip tone="amber">{scoreBand(created.priority_score)}</Chip>
              </Kv>
              <Kv k="RISK SCORE">
                <Score value={created.risk_score} />{" "}
                <Chip tone="amber">{scoreBand(created.risk_score)}</Chip>
              </Kv>
              <Kv k="SECTION · TRACK">
                {sectionOf(created) ?? "—"} · {created.track_id}
              </Kv>
              <Kv k="LOCATION">
                {locSource === "FIELD_LOCATION_VERIFIED"
                  ? "Location cross-check passed"
                  : String(locSource ?? "—")}
              </Kv>
              <Kv k="RECORDED">
                {recorded(created.created_at)} <Chip tone="amber">RECORDED</Chip>
              </Kv>
              {dup?.possible_duplicate && (
                <div className="rounded-DEFAULT bg-secondary-container/20 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
                  Advisory: this report resembles an open job (
                  {(dup.candidate_jobs ?? []).length} candidate). The job was
                  still created; nothing was merged.
                </div>
              )}
              <p className="font-label-mono text-[11px] text-outline">
                Deterministic risk scoring (baseline scorer, not a trained
                model). Open the job for the ranked explanation.
              </p>
              <div className="flex gap-space-sm">
                <Link href={`/jobs/${created.job_id}`} className={BTN_PRIMARY}>
                  Open job detail
                </Link>
                <Link href="/jobs" className={BTN_GHOST}>
                  Maintenance Jobs
                </Link>
              </div>
            </div>
          )}
        </div>
      </div>
    </Page>
  );
}

function Stepper({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-space-2xs">
      <span className={LABEL}>{label}</span>
      <div className="flex items-center bg-surface-container-lowest rounded-DEFAULT shadow-sm border border-outline-variant/30">
        <button
          type="button"
          aria-label={`Decrease ${label}`}
          className="px-3 py-2 text-on-surface-variant hover:text-primary disabled:opacity-30"
          disabled={value <= min}
          onClick={() => onChange(Math.max(min, value - 1))}
        >
          <span className="material-symbols-outlined text-[16px]">remove</span>
        </button>
        <span className="flex-1 text-center font-label-mono text-label-mono text-primary tabular-nums">
          {value}
        </span>
        <button
          type="button"
          aria-label={`Increase ${label}`}
          className="px-3 py-2 text-on-surface-variant hover:text-primary"
          onClick={() => onChange(value + 1)}
        >
          <span className="material-symbols-outlined text-[16px]">add</span>
        </button>
      </div>
    </div>
  );
}
