# NovaForge-mode authentication: design, configuration, console

Pashupatastra has two authentication modes. **Demo mode is the default and is complete on its
own.** NovaForge mode is optional; nothing in the planning, optimization or
disruption/recovery workflows depends on it. Do not enable it unless you have a NovaForge
instance with a registered client.

| | Demo mode (default) | NovaForge mode (`PASHUPAT_AUTH_MODE=novaforge`) |
|---|---|---|
| Identity | Declared via `X-Actor-Id` / `X-Actor-Role`, recorded `DECLARED_UNVERIFIED` | From a server-side session created by an OIDC login at NovaForge; recorded `AUTHENTICATED` |
| Authorization | Unenforced policy; console role gating is a convenience | `EnforcingPolicy` (roles, railway scope, corridor-complete) **plus** the NovaForge permission `job.<action>`; every route, server-side |
| `X-Actor-*` headers | Used | Refused (`ACTOR_HEADERS_NOT_ACCEPTED`) |
| Console | Persona switch in the header, labelled DECLARED (DEMO MODE) | Sign-in gate, role chooser, sign-out, labelled AUTHENTICATED (NOVAFORGE) |

## Flow (backend-for-frontend, authorization code + PKCE)

1. The console calls `GET /auth/config`. `{"mode":"demo"}` keeps the persona switch.
   `{"mode":"novaforge"}` makes the console stop sending declared identities.
2. `GET /auth/me` returns 401 -> the console shows **Sign in required**. The button navigates to
   `/auth/login?return_to=<path>`; the server redirects to NovaForge (state, nonce, PKCE, issuer
   check). The browser never sees a token or the client secret.
3. NovaForge redirects to `/auth/callback`; the server exchanges the code, verifies the ID token,
   creates a server-side session and sets an opaque `HttpOnly`, `SameSite=Lax` cookie. A failed
   sign-in redirects to `/auth/error?code=...`, which shows a plain message.
4. A user holding several NovaForge roles is asked to choose one (`POST /auth/session/role`;
   the session handle rotates).
5. Every state-changing request carries `X-PP-CSRF: 1` (the console adds it) and, if an `Origin`
   is present, it must equal the configured console origin.
6. Liveness is checked against NovaForge introspection within `PASHUPAT_AUTH_FRESHNESS_SECONDS`
   (fail closed). Revoked or expired sessions return 401; the console then shows the sign-in gate
   with "Your session ended". If NovaForge is unreachable the console shows **Sign-in is
   unavailable** and nothing else (fail closed).
7. **Sign out** calls `POST /auth/logout`, which ends the session here and returns NovaForge's
   end-session URL.

Protected operations are protected by the **backend**, not by the console gate: without a valid
session every `/v1` route returns 401, whatever the browser does.

## Same-origin proxy (required for the console)

The session cookie must belong to the console's origin, so the console reaches the backend through
a proxy (`frontend/next.config.ts`):

```
build the console with   NEXT_PUBLIC_API_URL=/api/pp   PASHUPAT_BACKEND_URL=http://127.0.0.1:8000
NovaForge redirect URI   <console origin>/api/pp/auth/callback
post-logout redirect     <console origin>/
```

`PASHUPAT_AUTH_FRONTEND_ORIGIN` must be the console origin; it is the only origin CORS and
redirects may use.

## Backend configuration

Required: `PASHUPAT_AUTH_MODE=novaforge`, `PASHUPAT_AUTH_ISSUER`, `PASHUPAT_AUTH_CLIENT_ID`,
`PASHUPAT_AUTH_CLIENT_SECRET`, `PASHUPAT_AUTH_AUDIENCE`, `PASHUPAT_AUTH_REDIRECT_URI`,
`PASHUPAT_AUTH_FRONTEND_ORIGIN`, `PASHUPAT_AUTH_JOBS_DB` (must not be the demo database).
Optional and defaults: see `backend/app/auth/settings.py`. Secrets live only in the server
environment: never in the console build, source, or commits. Missing or unsafe configuration
refuses to start; there is no fallback to demo mode.

## Required NovaForge-side change for disruption and recovery

NovaForge permissions can only **narrow** what Pashupatastra's own role table allows, and a
missing permission denies. NovaForge's provisioning
(`server/src/lib/oauth/provision.ts`, `PASHUPATASTRA_ACTIONS` and the role definitions) predates
the disruption actions, so until these are added and granted, **every disruption route returns
403 in NovaForge mode** (fail closed, by design):

| Permission | Grant to role |
|---|---|
| `job.register_disruption` | engineer |
| `job.clear_disruption` | engineer |
| `job.read_disruption` | engineer, authority |

Recovery uses `job.request_optimization`, which the engineer role already has. This change is in
the NovaForge repository and has **not** been made from this repository.

## What has and has not been verified

- Verified (automated): the backend flow against a simulated NovaForge
  (`backend/tests/test_novaforge_auth_http.py`), enforcing-policy gating of the disruption actions
  (`test_disruption_recovery.py`), and demo-mode behaviour of the console in a real browser.
- Verified (build): the console's sign-in gate, role chooser, sign-out, CSRF header and proxy
  compile, lint and build cleanly.
- **Not verified:** the sign-in gate and login redirect in a real browser against a running
  NovaForge, and the NovaForge-side cross-application test
  (`D:\NovaForge\server\test\pashupatastra-e2e.test.ts`, needs PostgreSQL). Both need a running
  NovaForge with a registered client.
