"use client";

import Link from "next/link";
import { Page, PageBanner, CARD, Chip } from "@/components/ui";

const IMPLEMENTED = [
  "Field intake with a station-and-offset location cross-check, idempotent submission and a derived asset association",
  "Deterministic baseline risk scoring (fixed weights) with the ranked explanation the backend records",
  "OR-Tools CP-SAT block optimization over possession windows derived from a synthetic timetable",
  "Block proposals with structured explanations; authority approve, postpone and reject with a reason",
  "Committed (approved) blocks pinned across re-optimization",
  "Execution start and completion with before-work and after-work evidence references",
  "Append-only job history and derived SLA obligations against an assumed policy",
];

const SIMULATED = [
  "The NDLS–AGC corridor: real station codes, but synthetic topology, chainage, timetable, asset condition and possession windows",
  "Every job in the seed, and every evidence reference (placeholders; no file is stored)",
  "Disruption Simulation runs on a separate synthetic fixture (Corridor A), statelessly, and saves nothing",
  "The spatial corridor visuals, orbital rings and glow are stylised. They are not live telemetry or train tracking",
];

const LIMITS = [
  "Identity is declared through request headers and recorded as DECLARED_UNVERIFIED. There is no login, authentication or enforced role-based access control. Role gating in this interface is a presentation convenience.",
  "The scorer is a deterministic baseline with hand-authored weights. It is not a trained machine-learning model.",
  "SLA durations are assumed demo engineering values, not Indian Railways policy. Obligations are computed when read; there is no scheduler or notification delivery.",
  "Approving a block sets the status value notified, but no notification is sent to anyone.",
  "Rejecting a proposal returns the job to the queue. The optimizer does not search for an alternative window and may propose the same placement again. Use Postpone to keep work out of a date.",
  "There are no train delay, ETA or live conflict features, no maps, and no evidence upload.",
];

export default function AboutPage() {
  return (
    <Page>
      <PageBanner
        serif
        eyebrow="SYSTEM // ABOUT THIS DEMO"
        title="About this demo"
        subtitle="Pashupatastra is a decision-support prototype for railway maintenance block planning, built for Smart India Hackathon 2026 (problem statement SIH26027)."
        right={<Chip tone="amber">SYNTHETIC / ILLUSTRATIVE DATA</Chip>}
      />

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">The problem</h2>
        <p className="font-body-md text-body-md text-on-surface-variant max-w-4xl">
          Maintenance needs track possession: a stretch of line closed to
          trains. On a dense trunk route that time is scarce, and placing work
          in it depends on manual coordination between field staff, engineers
          and the authority that grants the block. Pashupatastra keeps three
          things together: safety (work is placed only inside possession
          windows and committed blocks are pinned), accountability (each
          decision has a declared actor, a reason and a before/after state) and
          explainability (each score and placement carries structured reasons).
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-lg">
        <div className={`${CARD} flex flex-col gap-space-sm`}>
          <h2 className="font-headline-sm text-headline-sm text-primary">What is implemented</h2>
          <List items={IMPLEMENTED} />
        </div>
        <div className={`${CARD} flex flex-col gap-space-sm`}>
          <h2 className="font-headline-sm text-headline-sm text-secondary">What is simulated</h2>
          <List items={SIMULATED} />
        </div>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">Demo corridor and planning horizon</h2>
        <p className="font-body-md text-body-md text-on-surface-variant max-w-4xl">
          The demo corridor is NDLS–AGC (New Delhi, Hazrat Nizamuddin,
          Faridabad, Palwal, Mathura Junction, Raja Ki Mandi, Agra Cantt) with
          two tracks, UP-1 and DOWN-1. All placements are minutes from a fixed
          synthetic horizon start of 10 Sep 2026 00:00 IST, covering two days
          (10–11 Sep 2026). This is the plan date, not today&apos;s date.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-space-sm">
          <Clock tag="PLAN" tone="cyan" text="Horizon-relative minutes on the synthetic 10–11 Sep 2026 plan." />
          <Clock tag="OBSERVED" tone="violet" text="Times a crew member enters, such as actual start and evidence capture times." />
          <Clock tag="RECORDED" tone="amber" text="The server's own wall-clock time for events, reports and SLA clocks, shown in IST." />
        </div>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-secondary">Important limitations</h2>
        <List items={LIMITS} />
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">Data provenance vocabulary</h2>
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 font-body-sm text-body-sm text-on-surface-variant">
          <li><Chip tone="amber">SYNTHETIC</Chip> Invented or illustrative data checked into the repository.</li>
          <li><Chip tone="amber">SYSTEM DERIVED</Chip> Computed by the backend; inherits the weakest input provenance.</li>
          <li><Chip tone="neutral">DECLARED</Chip> Entered by a person in this session; identity not verified.</li>
          <li><Chip tone="violet">SIMULATION</Chip> What-if output that is not persisted or part of the lifecycle.</li>
        </ul>
        <p className="font-label-mono text-[11px] text-outline">
          Nothing in this build is verified against an authoritative railway source, and nothing is live.
        </p>
      </div>

      <div className="flex gap-space-md font-label-mono text-label-mono">
        <Link href="/" className="text-primary underline">Command Center</Link>
        <Link href="/inspection" className="text-primary underline">Start with a field report</Link>
      </div>
    </Page>
  );
}

function List({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((x) => (
        <li key={x} className="flex gap-2 font-body-sm text-body-sm text-on-surface-variant">
          <span className="material-symbols-outlined text-[16px] text-primary-fixed-dim shrink-0 mt-0.5">chevron_right</span>
          <span>{x}</span>
        </li>
      ))}
    </ul>
  );
}

function Clock({ tag, tone, text }: { tag: string; tone: "cyan" | "violet" | "amber"; text: string }) {
  return (
    <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
      <Chip tone={tone}>{tag}</Chip>
      <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">{text}</p>
    </div>
  );
}
