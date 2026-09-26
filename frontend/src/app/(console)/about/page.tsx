"use client";

import Link from "next/link";
import { Page, PageBanner, CARD, Chip } from "@/components/ui";
import DataBasisPanel, { ClassChip } from "@/components/DataBasisPanel";
import { IS_REAL, SNAPSHOT, pick } from "@/lib/railwayData";

const IMPLEMENTED = [
  "Field intake with a station-and-offset location cross-check, idempotent submission and a derived asset association",
  "Deterministic baseline risk scoring (fixed weights) with the ranked explanation the backend records",
  pick(
    "OR-Tools CP-SAT block optimization over candidate possession windows derived from the public TAG-2026 timetable",
    "OR-Tools CP-SAT block optimization over possession windows derived from a synthetic timetable",
  ),
  "Block proposals with structured explanations; authority approve, postpone and reject with a reason",
  "Committed (approved) blocks pinned across re-optimization",
  "Execution start and completion with before-work and after-work evidence references",
  "Append-only job history and derived SLA obligations against an assumed policy",
];

const SIMULATED = pick(
  [
    "Every maintenance observation in the seed and every evidence reference: controlled demo inputs (evidence references are placeholders; no file is stored)",
    "Asset condition, criticality and failure history: generated demo inputs. No public source exists",
    "The two modelled running lines and the scoring inputs (speed, density, route class): engineering assumptions",
    "Disruption Simulation runs on a separate synthetic fixture (Corridor A), statelessly, and saves nothing",
    "The spatial corridor visuals, orbital rings and glow are stylised. No train is drawn: there is no live telemetry or train tracking",
  ],
  [
    "The NDLS–AGC corridor: real station codes, but synthetic topology, chainage, timetable, asset condition and possession windows",
    "Every job in the seed, and every evidence reference (placeholders; no file is stored)",
    "Disruption Simulation runs on a separate synthetic fixture (Corridor A), statelessly, and saves nothing",
    "The spatial corridor visuals, orbital rings and glow are stylised. They are not live telemetry or train tracking",
  ],
);

const LIMITS = [
  "Identity is declared through request headers and recorded as DECLARED_UNVERIFIED. There is no login, authentication or enforced role-based access control. Role gating in this interface is a presentation convenience.",
  "The scorer is a deterministic baseline with hand-authored weights. It is not a trained machine-learning model.",
  "SLA durations are assumed demo engineering values, not Indian Railways policy. Obligations are computed when read; there is no scheduler or notification delivery.",
  "Approving a block sets the status value notified, but no notification is sent to anyone.",
  "Rejecting a proposal returns the job to the queue. The optimizer does not search for an alternative window and may propose the same placement again. Use Postpone to keep work out of a date.",
  "There are no train delay, ETA or live conflict features, no maps, and no evidence upload.",
  ...(IS_REAL
    ? [
        "Candidate possession windows come from a public passenger timetable subset. Freight, EMU/MEMU services and the working timetable are not published and are not included, so a window can overstate free track.",
        "Chainage is derived: whole kilometres from TAG-2026, or one decimal (marked ~) from official km-posts. Palwal–Mathura is an unresolved ~2 km conflict between sources; no finer precision is claimed.",
        "The station signal-interlocking plans are dated 2012–2025 and are shown with their own dates; they are not a statement about today's infrastructure.",
        "The real block notices (Faridabad, 2026; Mathura, 2024) are shown as evidence only. They are not applied to the plan and are not a possession feed.",
      ]
    : []),
];

const CLASS_HELP: [string, string][] = [
  ["REAL", "Stated directly by a public source (for example a station name or code)."],
  ["REAL_DATED_SNAPSHOT", "Read from a public document and kept as a dated snapshot (a timetable edition, a signal plan, a budget item)."],
  ["DERIVED_FROM_REAL", "Computed from real values by a stated method (for example chainage from published km, or a candidate possession window)."],
  ["ENGINEERING_ASSUMPTION", "A modelling choice, not a published fact (two modelled lines, safety buffer, scoring inputs)."],
  ["DEMO_MAINTENANCE_INPUT", "A controlled demonstration observation. It is not evidence that a defect exists."],
  ["UNAVAILABLE_PUBLICLY", "Searched for and not public: freight schedules, the working timetable, possession grants, defect-level asset data."],
];

export default function AboutPage() {
  return (
    <Page>
      <PageBanner
        serif
        eyebrow="SYSTEM // ABOUT THIS DEMO"
        title="About this demo"
        subtitle="Pashupatastra is a decision-support prototype for railway maintenance block planning, built for Smart India Hackathon 2026 (problem statement SIH26027)."
        right={
          <Chip tone="amber">
            {pick("PUBLIC SNAPSHOT + DEMO INPUTS", "SYNTHETIC / ILLUSTRATIVE DATA")}
          </Chip>
        }
      />

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">The problem</h2>
        <p className="font-body-md text-body-md text-on-surface-variant max-w-4xl">
          Maintenance needs track possession: a stretch of line closed to
          trains. On a dense trunk route that time is scarce, and placing work
          in it depends on manual coordination between field staff, engineers
          and the authority that grants the block. Pashupatastra keeps three
          things together: safety (work is placed only inside{" "}
          {pick("candidate possession", "possession")} windows and committed blocks are pinned), accountability (each
          decision has a declared actor, a reason and a before/after state) and
          explainability (each score and placement carries structured reasons).
        </p>
      </div>

      {IS_REAL && (
        <div className={`${CARD} flex flex-col gap-space-sm border border-primary/30`}>
          <h2 className="font-headline-sm text-headline-sm text-primary">Where the data comes from</h2>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-4xl" data-testid="about-data-statement">
            Pashupatastra uses published railway infrastructure and timetable data as an offline, dated reference
            layer. Operational possession windows are derived from the public timetable using explicit engineering
            rules. Maintenance observations shown in this demonstration are controlled field/demo inputs grounded in
            real railway work categories. No live or confidential Indian Railways operational feed is connected.
          </p>
        </div>
      )}

      <DataBasisPanel />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-lg">
        <div className={`${CARD} flex flex-col gap-space-sm`}>
          <h2 className="font-headline-sm text-headline-sm text-primary">What is implemented</h2>
          <List items={IMPLEMENTED} />
        </div>
        <div className={`${CARD} flex flex-col gap-space-sm`}>
          <h2 className="font-headline-sm text-headline-sm text-secondary">
            {pick("What is derived or a demo input", "What is simulated")}
          </h2>
          <List items={SIMULATED} />
        </div>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">Demo corridor and planning horizon</h2>
        <p className="font-body-md text-body-md text-on-surface-variant max-w-4xl">
          The demo corridor is NDLS–AGC (New Delhi, Hazrat Nizamuddin,
          Faridabad, Palwal, Mathura Junction, Raja Ki Mandi, Agra Cantt) with
          two modelled lines, UP-1 and DOWN-1.{" "}
          {pick(
            "UP means away from Delhi (NDLS → AGC) and DN toward Delhi, the railway's own convention; the identifiers UP-1 and DOWN-1 are kept for contract stability. ",
            "",
          )}
          All placements are minutes from a fixed horizon start of 10 Sep 2026
          00:00 IST, covering two days (10–11 Sep 2026). This is the plan date,
          not today&apos;s date.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-space-sm">
          <Clock tag="PLAN" tone="cyan" text={pick("Horizon-relative minutes on the 10–11 Sep 2026 plan.", "Horizon-relative minutes on the synthetic 10–11 Sep 2026 plan.")} />
          <Clock tag="OBSERVED" tone="violet" text="Times a crew member enters, such as actual start and evidence capture times." />
          <Clock tag="RECORDED" tone="amber" text="The server's own wall-clock time for events, reports and SLA clocks, shown in IST." />
        </div>
      </div>

      {IS_REAL && <SnapshotDetail />}

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-secondary">Important limitations</h2>
        <List items={LIMITS} />
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`}>
        <h2 className="font-headline-sm text-headline-sm text-primary">Data provenance vocabulary</h2>
        {IS_REAL ? (
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 font-body-sm text-body-sm text-on-surface-variant">
            {CLASS_HELP.map(([cls, help]) => (
              <li key={cls} className="flex flex-col gap-1">
                <span><ClassChip cls={cls} /></span>
                <span>{help}</span>
              </li>
            ))}
            <li className="flex flex-col gap-1">
              <span><Chip tone="amber">SYSTEM DERIVED</Chip></span>
              <span>Computed by the backend; inherits the weakest input provenance. With demo asset condition in the mix, an optimization run reports SYNTHETIC overall.</span>
            </li>
            <li className="flex flex-col gap-1">
              <span><Chip tone="neutral">DECLARED</Chip></span>
              <span>Entered by a person in this session; identity not verified.</span>
            </li>
            <li className="flex flex-col gap-1">
              <span><Chip tone="violet">SIMULATION</Chip></span>
              <span>What-if output that is not persisted or part of the lifecycle.</span>
            </li>
          </ul>
        ) : (
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 font-body-sm text-body-sm text-on-surface-variant">
            <li><Chip tone="amber">SYNTHETIC</Chip> Invented or illustrative data checked into the repository.</li>
            <li><Chip tone="amber">SYSTEM DERIVED</Chip> Computed by the backend; inherits the weakest input provenance.</li>
            <li><Chip tone="neutral">DECLARED</Chip> Entered by a person in this session; identity not verified.</li>
            <li><Chip tone="violet">SIMULATION</Chip> What-if output that is not persisted or part of the lifecycle.</li>
          </ul>
        )}
        <p className="font-label-mono text-[11px] text-outline">
          {pick(
            "Nothing is live. Each value keeps the class of its source; derived values are never shown as directly sourced railway data.",
            "Nothing in this build is verified against an authoritative railway source, and nothing is live.",
          )}
        </p>
      </div>

      <div className="flex gap-space-md font-label-mono text-label-mono">
        <Link href="/" className="text-primary underline">Corridor Overview</Link>
        <Link href="/inspection" className="text-primary underline">Start with a field report</Link>
      </div>
    </Page>
  );
}

function SnapshotDetail() {
  const T = SNAPSHOT.timetable;
  return (
    <>
      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-stations">
        <h2 className="font-headline-sm text-headline-sm text-primary">Corridor stations and chainage</h2>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Kilometres from New Delhi (a derived origin). Values marked ~ are derived from official km-posts; the others
          are whole kilometres from TAG-2026. Coordinates are OpenStreetMap contributors (ODbL), not railway coordinates.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left font-body-sm text-body-sm">
            <thead>
              <tr className="text-outline font-label-caps text-label-caps">
                <th className="py-1 pr-3">STATION</th>
                <th className="py-1 pr-3">KM</th>
                <th className="py-1 pr-3">CLASS</th>
                <th className="py-1 pr-3">CONFIDENCE</th>
                <th className="py-1 pr-3">ZONE · DIVISION (SECTION TO NEXT STATION)</th>
              </tr>
            </thead>
            <tbody>
              {SNAPSHOT.stations.map((s) => {
                const sec = SNAPSHOT.sections.find((x) => x.start === s.id);
                return (
                  <tr key={s.id} className="border-t border-outline-variant/15 text-on-surface-variant">
                    <td className="py-1 pr-3 text-on-surface">{s.id} · {s.name}</td>
                    <td className="py-1 pr-3 font-label-mono">{s.kmDisplay}</td>
                    <td className="py-1 pr-3"><ClassChip cls={s.kmClass} /></td>
                    <td className="py-1 pr-3">{s.kmConfidence}</td>
                    <td className="py-1 pr-3">{sec ? `${sec.zone} · ${sec.division}` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-timetable">
        <h2 className="font-headline-sm text-headline-sm text-primary">Timetable used: {T.title}</h2>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Effective {T.effective}. {T.trainCount} verified trains on the corridor; each was checked against the
          published table. Every train shown anywhere in this console is one of these. Times are the printed
          timetable, not live running.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left font-body-sm text-body-sm">
            <thead>
              <tr className="text-outline font-label-caps text-label-caps">
                <th className="py-1 pr-3">TRAIN</th>
                <th className="py-1 pr-3">DIR</th>
                <th className="py-1 pr-3">FROM → TO</th>
                <th className="py-1 pr-3">DAYS</th>
                <th className="py-1 pr-3">PRINTED TIMES ON THE CORRIDOR</th>
              </tr>
            </thead>
            <tbody>
              {T.trains.map((t) => (
                <tr key={t.number} className="border-t border-outline-variant/15 text-on-surface-variant align-top">
                  <td className="py-1 pr-3 text-on-surface whitespace-nowrap">{t.number} {t.name}</td>
                  <td className="py-1 pr-3 font-label-mono">{t.direction}</td>
                  <td className="py-1 pr-3">{t.from} → {t.to}</td>
                  <td className="py-1 pr-3 whitespace-nowrap">{t.days}</td>
                  <td className="py-1 pr-3 font-label-mono text-[11px]">
                    {t.stops
                      .map((s) => `${s.station} ${s.arr ?? ""}${s.arr && s.dep ? "/" : ""}${s.dep ?? ""}`.trim())
                      .join(" · ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="font-body-sm text-[12px] text-outline">Not included: {T.notIncluded.join("; ")}.</p>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-possession">
        <h2 className="font-headline-sm text-headline-sm text-primary">Possession windows</h2>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          <Chip tone="violet">{SNAPSHOT.possession.label}</Chip>
        </p>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Section occupancy from each timetabled train (with an interpolated passing time where the table prints none),
          extended by a {SNAPSHOT.possession.rules.safety_buffer_minutes}-minute safety buffer at each end; free gaps of at
          least {SNAPSHOT.possession.rules.minimum_window_minutes} minutes become candidate windows. These are engineering
          assumptions, not Indian Railways rules, and not a possession grant.
        </p>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-works">
        <h2 className="font-headline-sm text-headline-sm text-primary">Maintenance grounding</h2>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          GROUNDING: REAL RAILWAY WORK / PROJECT. {SNAPSHOT.worksWording.split(". ").slice(1).join(". ")}
        </p>
        <ul className="flex flex-col gap-2 font-body-sm text-body-sm text-on-surface-variant">
          {SNAPSHOT.works.map((w) => (
            <li key={w.demoJobKey}>
              <span className="text-on-surface">{w.demoJobKey}</span> · {w.jobType} · {w.sectionId}: {w.summary}{" "}
              <span className="text-outline">
                ({w.items.map((i) => `${i.zone} budget item ${i.itemNo}, p.${i.page}`).join("; ")})
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-blocks">
        <h2 className="font-headline-sm text-headline-sm text-primary">Real block notices (evidence only)</h2>
        <ul className="flex flex-col gap-2 font-body-sm text-body-sm text-on-surface-variant">
          {SNAPSHOT.blocks.map((b) => (
            <li key={b.id}>
              <ClassChip cls={b.class} /> <span className="text-on-surface">{b.location}</span> · {b.from} to {b.to} ({b.status}).{" "}
              {b.note} Not applied to the plan; not a live feed.
            </li>
          ))}
        </ul>
      </div>

      <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="about-sources">
        <h2 className="font-headline-sm text-headline-sm text-primary">Sources</h2>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Retrieved {SNAPSHOT.retrievedAt}. Each source is stored locally with its SHA-256 and checked when the backend starts; nothing is fetched at run time.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left font-body-sm text-body-sm">
            <thead>
              <tr className="text-outline font-label-caps text-label-caps">
                <th className="py-1 pr-3">SOURCE</th>
                <th className="py-1 pr-3">PUBLISHER</th>
                <th className="py-1 pr-3">DATA AS OF</th>
                <th className="py-1 pr-3">CURRENCY</th>
                <th className="py-1 pr-3">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {SNAPSHOT.sources.map((s) => (
                <tr key={s.id} className="border-t border-outline-variant/15 text-on-surface-variant align-top">
                  <td className="py-1 pr-3 text-on-surface">{s.name}</td>
                  <td className="py-1 pr-3">{s.publisher}</td>
                  <td className="py-1 pr-3 whitespace-nowrap font-label-mono">{s.dataAsOf}</td>
                  <td className="py-1 pr-3">{s.currency}</td>
                  <td className="py-1 pr-3 font-label-mono text-[10px]">{s.sha256.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
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
