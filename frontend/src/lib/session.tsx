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
  API_BASE_URL,
  ApiError,
  PERSONAS,
  UNAUTHENTICATED_EVENT,
  api,
  setEnforcedAuth,
  type AuthMe,
  type OptimizationResponse,
  type Persona,
  type Role,
} from "@/lib/api";

/**
 * Who the console is acting as.
 *  - demo: identity is DECLARED through the persona switch (not verified).
 *  - novaforge: identity comes only from the server-side session. status is
 *    "loading" until /auth/me answers, "anonymous" (sign in), "selecting" (the
 *    user holds several roles and must choose one), "ready", or "unavailable"
 *    (the identity provider or the backend could not be reached: fail closed).
 */
export type AuthState =
  | { mode: "loading" }
  | { mode: "demo" }
  | {
      mode: "novaforge";
      status: "loading" | "anonymous" | "selecting" | "ready" | "unavailable";
      me: AuthMe | null;
      error: string | null;
    };

interface Session {
  auth: AuthState;
  signIn: () => void;
  signOut: () => Promise<void>;
  chooseRole: (role: Role) => Promise<void>;
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
  const [auth, setAuth] = useState<AuthState>({ mode: "loading" });

  const refreshMe = useCallback(async () => {
    try {
      const me = await api.me();
      setAuth({
        mode: "novaforge",
        status: me.selection_required ? "selecting" : "ready",
        me,
        error: null,
      });
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err && err.status === 401) {
        setAuth({ mode: "novaforge", status: "anonymous", me: null, error: null });
      } else {
        setAuth({
          mode: "novaforge",
          status: "unavailable",
          me: null,
          error: err ? `${err.code}: ${err.detail}` : "The backend could not be reached.",
        });
      }
    }
  }, []);

  // Ask the server which mode it runs in. Demo mode changes nothing.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cfg = await api.authConfig();
        if (!alive) return;
        if (cfg.mode === "novaforge") {
          setEnforcedAuth(true);
          setAuth({ mode: "novaforge", status: "loading", me: null, error: null });
          await refreshMe();
        } else {
          setEnforcedAuth(false);
          setAuth({ mode: "demo" });
        }
      } catch {
        // Older or unreachable backend: the connectivity probe below reports
        // it. Fall back to demo mode only, never to a weaker enforced mode.
        if (alive) setAuth({ mode: "demo" });
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshMe]);

  // Any 401 from an enforced-mode call means the session ended or expired.
  useEffect(() => {
    const onExpired = () => {
      setAuth((cur) =>
        cur.mode === "novaforge"
          ? { mode: "novaforge", status: "anonymous", me: null, error: "Your session ended. Sign in again." }
          : cur,
      );
    };
    window.addEventListener(UNAUTHENTICATED_EVENT, onExpired);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onExpired);
  }, []);

  const signIn = useCallback(() => {
    // A full-page navigation: the server redirects to NovaForge and back.
    const back = encodeURIComponent(window.location.pathname || "/");
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a server route (via the same-origin proxy), not a Next.js page
    window.location.assign(`${API_BASE_URL}/auth/login?return_to=${back}`);
  }, []);

  const signOut = useCallback(async () => {
    try {
      const r = await api.logout();
      setAuth({ mode: "novaforge", status: "anonymous", me: null, error: null });
      if (r.logout_url) window.location.assign(r.logout_url);
    } catch {
      // The session cookie may already be gone; re-read the truth.
      await refreshMe();
    }
  }, [refreshMe]);

  const chooseRole = useCallback(
    async (role: Role) => {
      try {
        await api.selectRole(role);
      } catch {
        /* refreshMe below reports the resulting state */
      }
      await refreshMe();
      setTick((t) => t + 1);
    },
    [refreshMe],
  );

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
      auth,
      signIn,
      signOut,
      chooseRole,
      persona:
        auth.mode === "novaforge" && auth.me && auth.me.active_role
          ? {
              id: auth.me.actor_id,
              role: auth.me.active_role,
              label: auth.me.name ?? auth.me.actor_id,
            }
          : (PERSONAS.find((p) => p.id === personaId) ?? PERSONAS[0]),
      setPersonaId,
      online,
      lastOptimize: opt.last,
      previousOptimize: opt.prev,
      recordOptimize,
      tick,
      bump,
    }),
    [auth, signIn, signOut, chooseRole, personaId, setPersonaId, online, opt, recordOptimize, tick, bump],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
