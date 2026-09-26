"use client";

/**
 * Shared presentation primitives built from the Stitch class vocabulary
 * (glass panels, mono labels, thin technical borders, cyan/amber accents).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { ApiError, STATUS_LABEL, type JobStatus } from "@/lib/api";
import { pick } from "@/lib/railwayData";

export const CARD =
  "p-space-lg rounded-DEFAULT bg-surface-container/70 backdrop-blur-2xl shadow-xl";
export const CARD_INNER = "rounded-DEFAULT bg-surface-container-lowest";
export const LABEL =
  "font-label-caps text-label-caps text-outline uppercase tracking-wider";
export const INPUT =
  "w-full bg-surface-container-lowest text-on-surface px-space-md py-space-sm rounded-DEFAULT font-label-mono text-label-mono focus:outline-none focus:bg-surface-container-low shadow-sm border border-outline-variant/30 focus:border-primary-container/60";
export const BTN_PRIMARY =
  "px-5 py-2.5 rounded-full bg-primary text-on-primary font-label-mono text-label-mono font-medium hover:bg-primary-fixed transition-all shadow-[0_0_20px_rgba(0,219,233,0.3)] flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none";
export const BTN_GHOST =
  "px-4 py-2 rounded-full bg-surface-container-high text-on-surface-variant hover:text-primary hover:bg-surface-bright font-label-mono text-label-mono transition-all flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed";
export const BTN_WARN =
  "px-5 py-2.5 rounded-full bg-secondary text-on-secondary font-label-mono text-label-mono font-medium hover:brightness-110 transition-all flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed";
export const BTN_DANGER =
  "px-5 py-2.5 rounded-full bg-error text-on-error font-label-mono text-label-mono font-medium hover:brightness-110 transition-all flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed";

/** Halo + slow radar sweep used behind every screen. Purely decorative. */
export function Ambient() {
  return (
    <>
      <div
        aria-hidden
        className="absolute -top-32 right-1/4 w-[600px] h-[600px] rounded-full bg-primary-container/5 blur-[120px] pointer-events-none -z-10 animate-pulse"
        style={{ animationDuration: "8s" }}
      />
      <div
        aria-hidden
        className="absolute top-1/2 -left-40 w-[500px] h-[500px] rounded-full bg-secondary-container/10 blur-[140px] pointer-events-none -z-10"
      />
      <svg
        aria-hidden
        className="absolute top-12 right-6 w-96 h-96 opacity-10 pointer-events-none -z-10"
        viewBox="0 0 200 200"
      >
        <circle className="text-primary-fixed-dim" cx="100" cy="100" fill="none" r="90" stroke="currentColor" strokeDasharray="2 4" strokeWidth="0.5" />
        <circle className="text-outline" cx="100" cy="100" fill="none" r="60" stroke="currentColor" strokeWidth="0.5" />
        <circle className="text-primary-fixed-dim" cx="100" cy="100" fill="none" r="30" stroke="currentColor" strokeWidth="0.5" />
        <line className="text-outline" stroke="currentColor" strokeWidth="0.5" x1="100" x2="100" y1="5" y2="195" />
        <line className="text-outline" stroke="currentColor" strokeWidth="0.5" x1="5" x2="195" y1="100" y2="100" />
        <line className="text-primary-fixed-dim opacity-70" stroke="currentColor" strokeWidth="1.2" x1="100" x2="170" y1="100" y2="30">
          <animateTransform attributeName="transform" dur="20s" from="0 100 100" repeatCount="indefinite" to="360 100 100" type="rotate" />
        </line>
      </svg>
    </>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex flex-col w-full overflow-hidden pt-space-md pb-space-2xl gap-space-lg">
      <Ambient />
      {children}
    </div>
  );
}

export function PageBanner({
  eyebrow,
  title,
  subtitle,
  right,
  serif = false,
}: {
  eyebrow: string;
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  serif?: boolean;
}) {
  return (
    <section className="flex flex-wrap items-end justify-between gap-space-md p-space-md rounded-DEFAULT bg-surface-container-low/90 backdrop-blur-xl shadow-xl">
      <div className="flex flex-col gap-1 min-w-0">
        <div className="flex items-center gap-space-xs font-label-caps text-label-caps text-primary-fixed-dim">
          <span className="inline-block w-2 h-2 rounded-full bg-primary-container shadow-[0_0_8px_#00f0ff]" />
          <span>{eyebrow}</span>
        </div>
        <h1
          className={
            serif
              ? "font-headline-lg text-headline-lg text-primary font-light tracking-tight"
              : "font-headline-md text-headline-md tracking-tight text-primary"
          }
        >
          {title}
        </h1>
        {subtitle && (
          <p className="font-body-md text-body-md text-on-surface-variant max-w-3xl">
            {subtitle}
          </p>
        )}
      </div>
      {right && (
        <div className="flex flex-wrap items-center gap-space-sm">{right}</div>
      )}
    </section>
  );
}

/* ---------- chips & tags ---------- */

const STATUS_STYLE: Record<JobStatus, string> = {
  reported: "bg-surface-container-high text-on-surface-variant",
  scheduled: "bg-secondary-container/30 text-secondary",
  notified: "bg-primary-container/15 text-primary-fixed",
  in_progress: "bg-tertiary-container/20 text-tertiary",
  completed: "bg-primary-container/25 text-primary",
};

export function StatusChip({ status }: { status: JobStatus | string }) {
  const s = status as JobStatus;
  return (
    <span
      title={`raw status: ${status}`}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-label-mono text-label-mono whitespace-nowrap ${STATUS_STYLE[s] ?? "bg-surface-container-high text-on-surface-variant"}`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {STATUS_LABEL[s] ?? status}
    </span>
  );
}

const SEV_STYLE: Record<string, string> = {
  CRITICAL: "bg-error-container/50 text-error",
  MODERATE: "bg-secondary-container/30 text-secondary",
  MINOR: "bg-surface-container-high text-on-surface-variant",
  NONE: "bg-surface-container-high text-outline",
};

export function SeverityChip({ severity }: { severity?: string | null }) {
  if (!severity) return <span className="text-outline">—</span>;
  return (
    <span
      className={`px-2 py-0.5 rounded font-label-caps text-label-caps font-bold ${SEV_STYLE[severity] ?? SEV_STYLE.NONE}`}
    >
      {severity}
    </span>
  );
}

export function Chip({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "cyan" | "amber" | "violet" | "red";
  title?: string;
}) {
  const tones = {
    neutral: "bg-surface-container-high text-on-surface-variant",
    cyan: "bg-primary-container/15 text-primary-fixed",
    amber: "bg-secondary-container/30 text-secondary",
    violet: "bg-tertiary-container/20 text-tertiary",
    red: "bg-error-container/50 text-error",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded font-label-caps text-label-caps ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Clock tag: PLAN / RECORDED / OBSERVED. */
export function TimeTag({ kind }: { kind: "PLAN" | "RECORDED" | "OBSERVED" }) {
  const tone =
    kind === "PLAN" ? "cyan" : kind === "RECORDED" ? "amber" : "violet";
  const help =
    kind === "PLAN"
      ? pick(
          "Plan time: minutes on the fixed planning horizon (10–11 Sep 2026, IST)",
          "Plan time: minutes on the fixed synthetic horizon (10–11 Sep 2026, IST)",
        )
      : kind === "RECORDED"
        ? "Recorded: server wall-clock time, shown in IST"
        : "Observed: time entered by the crew";
  return (
    <Chip tone={tone} title={help}>
      {kind}
    </Chip>
  );
}

export function ProvenanceChip({
  label = pick(
    "SYSTEM DERIVED · public snapshot + DEMO inputs",
    "SYSTEM DERIVED · from SYNTHETIC inputs",
  ),
  tone = "amber",
}: {
  label?: string;
  tone?: "amber" | "violet" | "neutral";
}) {
  return <Chip tone={tone}>{label}</Chip>;
}

export function Score({ value }: { value: number }) {
  return (
    <span className="font-label-mono text-label-mono tabular-nums">
      {value.toFixed(3)}
    </span>
  );
}

/* ---------- state panels ---------- */

export function ErrorBox({
  error,
  title,
}: {
  error: ApiError | Error | string | null;
  title?: string;
}) {
  if (!error) return null;
  const isApi = error instanceof ApiError;
  const code = isApi ? error.code : null;
  const detail = isApi ? error.detail : typeof error === "string" ? error : error.message;
  return (
    <div
      role="alert"
      className="rounded-DEFAULT border border-error/40 bg-error-container/25 px-space-md py-space-sm"
    >
      <div className="flex items-center gap-2 font-label-caps text-label-caps text-error">
        <span className="material-symbols-outlined text-[16px]">error</span>
        <span>{title ?? "REQUEST REFUSED"}</span>
        {code && <span className="font-label-mono text-label-mono">{code}</span>}
      </div>
      <p className="font-body-sm text-body-sm text-on-error-container mt-1 break-words">
        {detail}
      </p>
    </div>
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-DEFAULT border border-dashed border-outline-variant/50 px-space-lg py-space-xl text-center">
      <div className="font-headline-sm text-headline-sm text-on-surface-variant">
        {title}
      </div>
      {children && (
        <div className="font-body-sm text-body-sm text-outline mt-1">
          {children}
        </div>
      )}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-9 rounded skeleton-shimmer" />
      ))}
    </div>
  );
}

export function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin"
    />
  );
}

export function Kv({
  k,
  children,
  mono = true,
}: {
  k: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-b border-outline-variant/15 last:border-0">
      <span className="font-label-caps text-label-caps text-outline shrink-0">
        {k}
      </span>
      <span
        className={`${mono ? "font-label-mono text-label-mono" : "font-body-sm text-body-sm"} text-on-surface text-right break-words min-w-0`}
      >
        {children}
      </span>
    </div>
  );
}

/* ---------- modal ---------- */

export function Modal({
  open,
  title,
  onClose,
  children,
  wide = false,
  busy = false,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Callers pass a fresh onClose every render; keep the latest in a ref so the
  // effect below runs only on open/close and does not re-focus the dialog on
  // every keystroke (which stole focus from the inputs).
  const latest = useRef({ onClose, busy });
  useEffect(() => {
    latest.current = { onClose, busy };
  });
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !latest.current.busy) latest.current.onClose();
    };
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  if (!open || typeof document === "undefined") return null;
  // Portalled to <body> so the scrim covers the fixed header and sidebar too.
  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-[#090d16]/75 backdrop-blur-sm p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`relative mt-10 w-full ${wide ? "max-w-3xl" : "max-w-xl"} rounded-DEFAULT bg-surface-container shadow-2xl border border-outline-variant/40 outline-none`}
      >
        <div className="flex items-center justify-between px-space-lg py-space-md border-b border-outline-variant/30">
          <h2 className="font-headline-sm text-headline-sm text-primary">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close dialog"
            className="text-outline hover:text-on-surface disabled:opacity-40"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>
        <div className="px-space-lg py-space-md flex flex-col gap-space-md">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function DemoModeNote() {
  return (
    <p className="font-label-mono text-[11px] leading-4 text-outline border-l-2 border-secondary/50 pl-2">
      Demo mode: role restrictions are not enforced by the server. The
      identity is declared via request headers and recorded as
      DECLARED_UNVERIFIED. This is not production authorization.
    </p>
  );
}

/* ---------- toast ---------- */

interface ToastMsg {
  id: number;
  tone: "ok" | "warn";
  text: string;
}
const ToastCtx = createContext<(text: string, tone?: "ok" | "warn") => void>(
  () => {},
);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastMsg[]>([]);
  const push = useCallback((text: string, tone: "ok" | "warn" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((cur) => [...cur, { id, tone, text }]);
    setTimeout(() => setItems((cur) => cur.filter((t) => t.id !== id)), 7000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        className="fixed bottom-12 right-6 z-[90] flex flex-col gap-2 max-w-md"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className={`rounded-DEFAULT px-space-md py-space-sm shadow-2xl backdrop-blur-xl border font-body-sm text-body-sm ${t.tone === "ok" ? "bg-surface-container-high/95 border-primary-container/50 text-primary-fixed" : "bg-surface-container-high/95 border-secondary/50 text-secondary"}`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}
