"use client";

/**
 * Session state shared by every screen:
 *  - the declared persona (demo mode: identity is declared, not verified)
 *  - backend connectivity (/health) - mutations are disabled while offline
 *  - the last two optimize responses, used for the re-optimization diff
 *  - a refresh tick bumped after every mutation so screens re-read the server
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  PERSONAS,
  api,
  type OptimizationResponse,
  type Persona,
} from "@/lib/api";

interface Session {
  persona: Persona;
  setPersonaId: (id: string) => void;
  online: boolean | null;
  lastOptimize: OptimizationResponse | null;
  previousOptimize: OptimizationResponse | null;
  recordOptimize: (r: OptimizationResponse) => void;
  tick: number;
  bump: () => void;
}

const Ctx = createContext<Session | null>(null);

const PERSONA_KEY = "pashupatastra.persona";
const OPT_KEY = "pashupatastra.optimize";

function readStore<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeStore(key: string, value: unknown) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: session state simply stays in memory */
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [personaId, setPersonaIdState] = useState(PERSONAS[0].id);
  const [online, setOnline] = useState<boolean | null>(null);
  const [opt, setOpt] = useState<{
    last: OptimizationResponse | null;
    prev: OptimizationResponse | null;
  }>({ last: null, prev: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = readStore<string>(PERSONA_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- restore persisted session state after mount
    if (id && PERSONAS.some((p) => p.id === id)) setPersonaIdState(id);
    const saved = readStore<{
      last: OptimizationResponse | null;
      prev: OptimizationResponse | null;
    }>(OPT_KEY);
    if (saved) setOpt(saved);
  }, []);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      const ok = await api.health();
      if (alive) setOnline(ok);
    };
    probe();
    const t = setInterval(probe, 15_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const setPersonaId = useCallback((id: string) => {
    setPersonaIdState(id);
    writeStore(PERSONA_KEY, id);
  }, []);

  const recordOptimize = useCallback((r: OptimizationResponse) => {
    setOpt((cur) => {
      const next = { last: r, prev: cur.last };
      writeStore(OPT_KEY, next);
      return next;
    });
  }, []);

  const bump = useCallback(() => setTick((t) => t + 1), []);

  const value = useMemo<Session>(
    () => ({
      persona: PERSONAS.find((p) => p.id === personaId) ?? PERSONAS[0],
      setPersonaId,
      online,
      lastOptimize: opt.last,
      previousOptimize: opt.prev,
      recordOptimize,
      tick,
      bump,
    }),
    [personaId, setPersonaId, online, opt, recordOptimize, tick, bump],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
