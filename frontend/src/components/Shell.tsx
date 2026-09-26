"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { API_BASE_URL, CORRIDOR_ID, PERSONAS } from "@/lib/api";
import { useSession } from "@/lib/session";
import { nowClockIst } from "@/lib/time";
import { pick } from "@/lib/railwayData";

interface NavItem {
  href: string;
  label: string;
  icon: string;
  badge?: string;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "OPERATE",
    items: [
      { href: "/", label: "Corridor Overview", icon: "space_dashboard" },
      { href: "/inspection", label: "Field Inspection", icon: "edit_location_alt" },
      { href: "/jobs", label: "Maintenance Jobs", icon: "format_list_bulleted" },
      { href: "/planning", label: "Planning", icon: "calendar_clock" },
      { href: "/review", label: "Authority Review", icon: "verified_user" },
      { href: "/execution", label: "Execution", icon: "engineering" },
    ],
  },
  {
    group: "ASSURANCE",
    items: [
      { href: "/audit", label: "Audit Trail", icon: "history_edu" },
      { href: "/obligations", label: "Obligations (SLA)", icon: "policy" },
    ],
  },
  {
    group: "SIMULATION",
    items: [
      { href: "/simulation", label: "Disruption Sim", icon: "bolt", badge: "SIM" },
    ],
  },
  {
    group: "SYSTEM",
    items: [{ href: "/about", label: "About This Demo", icon: "info" }],
  },
];

const ACTIVE =
  "bg-surface-container-high text-primary-fixed border-l-2 border-primary-container shadow-[inset_0_0_12px_rgba(0,219,233,0.15)] font-headline-sm";
const IDLE =
  "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { persona, setPersonaId, online } = useSession();
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial client-only clock (avoids a hydration mismatch)
    setClock(nowClockIst());
    const t = setInterval(() => setClock(nowClockIst()), 1000);
    return () => clearInterval(t);
  }, []);

  const backendText =
    online === null ? "CHECKING" : online ? "● CONNECTED" : "○ OFFLINE";
  const backendColor =
    online === null
      ? "text-outline"
      : online
        ? "text-primary-container"
        : "text-error";

  return (
    <div className="bg-background font-body-md text-on-surface antialiased selection:bg-primary-container selection:text-on-primary-container min-h-screen relative overflow-x-hidden">
      {/* ambient depth: glow + orbital rings (decorative) */}
      <div
        aria-hidden
        className="fixed inset-0 pointer-events-none z-0 overflow-hidden"
      >
        <div className="absolute -top-40 left-1/4 w-[750px] h-[750px] rounded-full bg-[radial-gradient(circle,rgba(0,219,233,0.06)_0%,transparent_70%)] blur-3xl" />
        <div className="absolute top-1/3 -right-32 w-[600px] h-[600px] rounded-full bg-[radial-gradient(circle,rgba(255,182,136,0.04)_0%,transparent_70%)] blur-3xl" />
        <div className="absolute top-12 left-64 w-[500px] h-[500px] rounded-full border border-outline-variant/10" />
        <div className="absolute top-28 left-80 w-[350px] h-[350px] rounded-full border border-outline-variant/10" />
      </div>

      <header className="fixed top-0 left-0 right-0 h-14 z-50 bg-surface-container-lowest/90 backdrop-blur-2xl shadow-[0_1px_12px_rgba(0,0,0,0.5)] flex items-center justify-between px-4">
        <div className="flex items-center gap-3 shrink-0">
          <Link href="/" className="flex items-center gap-3" aria-label="Corridor Overview">
            <Image src="/logo.svg" alt="Pashupatastra emblem" width={30} height={36} className="h-7 w-auto object-contain" priority />
            <div className="flex flex-col">
              <span className="font-headline-lg text-headline-sm tracking-wide text-primary leading-none">
                PASHUPATASTRA
              </span>
              <span className="font-label-caps text-label-caps text-outline leading-tight mt-0.5">
                RAILWAY DSS CONSOLE
              </span>
            </div>
          </Link>
          <div className="h-4 w-px bg-outline-variant/60 mx-1" />
          <span className="font-label-mono text-label-mono px-2 py-0.5 rounded-full bg-surface-container-high text-primary-fixed border border-outline-variant/40 tracking-wider">
            {CORRIDOR_ID}
          </span>
          <span className="font-label-caps text-label-caps text-on-surface-variant hidden xl:inline-block tracking-widest">
            {pick(
              "TIMETABLE: TAG-2026 · POSSESSION: DERIVED (10–11 SEP 2026)",
              "SYNTHETIC TIMETABLE (10-11 SEP 2026)",
            )}
          </span>
        </div>

        <div className="hidden lg:flex items-center gap-2">
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-container-low border border-outline-variant/30">
            <span
              className={`w-1.5 h-1.5 rounded-full ${online ? "bg-primary-container animate-pulse" : online === false ? "bg-error" : "bg-outline"}`}
            />
            <span className="font-label-mono text-label-mono text-on-surface-variant">
              BACKEND: <span className={backendColor}>{backendText}</span>
            </span>
          </div>
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-container-low border border-outline-variant/30">
            <span className="font-label-mono text-label-mono text-on-surface-variant">
              DATA:{" "}
              <span className="text-secondary" title={pick("Published railway data as a dated offline snapshot. Maintenance observations are demo inputs. No live feed.", "Synthetic demo dataset")}>
                {pick("PUBLIC SNAPSHOT · DEMO INPUTS", "SYNTHETIC")}
              </span>
            </span>
          </div>
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-container-low border border-outline-variant/30"
            title="Demo mode: identity is declared through request headers and recorded as DECLARED_UNVERIFIED. The server does not authenticate or enforce roles. Role gating in this UI is a presentation convenience, not production authorization."
          >
            <span className="font-label-mono text-label-mono text-on-surface-variant">
              IDENTITY: <span className="text-outline">DECLARED (DEMO MODE)</span>
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div
            role="group"
            aria-label="Acting persona (declared identity, demo mode)"
            className="flex items-center bg-surface-container-low rounded-full p-0.5 border border-outline-variant/40"
          >
            {PERSONAS.map((p) => (
              <button
                key={p.id}
                type="button"
                title={`${p.label} — declared, not verified`}
                aria-pressed={p.id === persona.id}
                onClick={() => setPersonaId(p.id)}
                className={
                  p.id === persona.id
                    ? "font-label-mono text-label-mono px-2 py-0.5 rounded-full bg-surface-container-high text-primary transition-colors"
                    : "font-label-mono text-label-mono px-2 py-0.5 rounded-full text-on-surface-variant hover:text-on-surface transition-colors"
                }
              >
                {p.id}
              </button>
            ))}
          </div>
          <div className="hidden md:flex items-center px-2 py-1 rounded bg-surface-container-lowest border border-outline-variant/30 text-on-surface">
            <span className="font-label-mono text-label-mono tracking-tight text-primary-fixed">
              {clock} IST
            </span>
            <span className="font-label-caps text-label-caps text-secondary ml-1.5 opacity-90">
              [LOCAL]
            </span>
          </div>
        </div>
      </header>

      <aside className="fixed left-0 top-14 bottom-8 w-60 z-40 bg-surface-container-lowest/85 backdrop-blur-2xl flex flex-col justify-between py-4 overflow-y-auto border-r border-outline-variant/40 shadow-[4px_0_24px_rgba(0,0,0,0.4)]">
        <nav className="flex flex-col gap-4 px-2.5">
          {NAV.map((g) => (
            <div key={g.group} className="flex flex-col gap-1">
              <span className="font-label-caps text-label-caps text-outline/70 px-3 py-1 tracking-widest">
                {g.group}
              </span>
              {g.items.map((it) => {
                const active = isActive(pathname, it.href);
                return (
                  <Link
                    key={it.href}
                    href={it.href}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center justify-between px-3 py-1.5 rounded transition-all ${active ? ACTIVE : IDLE}`}
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="material-symbols-outlined text-base">
                        {it.icon}
                      </span>
                      <span className="font-body-sm text-body-sm">{it.label}</span>
                    </div>
                    {it.badge && (
                      <span className="font-label-caps text-label-caps px-1.5 py-0.5 rounded bg-on-tertiary text-tertiary font-bold tracking-wider">
                        {it.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="px-4 py-2 mt-4 mx-2 rounded-lg bg-surface-container-low border border-outline-variant/30 flex flex-col gap-1">
          <div className="flex items-center justify-between text-outline">
            <span className="font-label-caps text-label-caps">API LINK</span>
            <span
              className={`w-1.5 h-1.5 rounded-full ${online ? "bg-surface-tint" : "bg-error"}`}
            />
          </div>
          <span className="font-label-mono text-label-mono text-on-surface-variant break-all">
            {API_BASE_URL.replace(/^https?:\/\//, "")}
          </span>
          <span className="font-label-mono text-label-mono text-outline">
            ACTING AS {persona.role}
          </span>
        </div>
      </aside>

      <div className="pl-60 min-h-screen flex flex-col justify-between relative z-10">
        <main className="w-full pt-14 pb-12 px-6 flex-1 bg-transparent">
          {online === false && (
            <div
              role="alert"
              className="mt-4 rounded-lg border border-error/50 bg-error-container/30 px-4 py-2 font-label-mono text-label-mono text-error"
            >
              BACKEND OFFLINE — {API_BASE_URL} is not answering. Actions are
              disabled and no fixture data is substituted on lifecycle screens.
            </div>
          )}
          <div className="flex flex-col w-full text-on-surface">{children}</div>
        </main>
      </div>

      <footer className="fixed bottom-0 left-60 right-0 h-8 z-40 bg-surface-container-lowest/95 backdrop-blur-md border-t border-outline-variant/30 flex items-center justify-between px-6">
        <div className="flex items-center gap-3 overflow-hidden">
          <span className="w-1.5 h-1.5 rounded-full bg-primary-container shrink-0" />
          <span className="font-label-mono text-label-mono text-outline tracking-wider truncate">
            PASHUPATASTRA • CORRIDOR: NDLS-AGC • SOLVER: OR-TOOLS CP-SAT •{" "}
            {pick(
              "DATED PUBLIC SNAPSHOT + DEMO MAINTENANCE INPUTS — NO LIVE INDIAN RAILWAYS FEED",
              "STRICTLY SYNTHETIC DEMO DATA — NOT LIVE INDIAN RAILWAYS DATA",
            )}
          </span>
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <span className="font-label-caps text-label-caps text-on-surface-variant tracking-widest">
            IDENTITY // DECLARED, NOT VERIFIED
          </span>
        </div>
      </footer>
    </div>
  );
}
