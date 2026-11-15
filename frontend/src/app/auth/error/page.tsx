"use client";

/**
 * Where the backend sends the browser when a NovaForge sign-in fails
 * (GET /auth/callback redirects to /auth/error?code=...). Shows a plain
 * message and a way back; it never displays anything the identity provider
 * sent beyond a short machine code.
 */

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

const MESSAGES: Record<string, string> = {
  access_denied: "Sign-in was cancelled or refused at NovaForge.",
  invalid_state: "The sign-in attempt expired or did not match. Start again.",
  state_mismatch: "The sign-in attempt expired or did not match. Start again.",
  provider_unavailable: "NovaForge could not be reached. Try again shortly.",
  unauthenticated: "Sign-in could not be completed. Start again.",
};

function Body() {
  const code = (useSearchParams().get("code") ?? "").slice(0, 64);
  const known = MESSAGES[code];
  return (
    <main className="min-h-screen flex items-center justify-center bg-background text-on-surface p-6">
      <div className="max-w-md rounded-lg bg-surface-container/80 border border-outline-variant/40 p-8 flex flex-col gap-4">
        <h1 className="font-headline-md text-headline-md text-primary">Sign-in failed</h1>
        <p role="alert" className="font-body-md text-body-md text-on-surface-variant">
          {known ?? "The sign-in could not be completed."}
        </p>
        {code && <p className="font-label-mono text-label-mono text-outline">code: {code.replace(/[^a-z0-9_]/gi, "")}</p>}
        <Link href="/" className="self-start px-5 py-2.5 rounded-full bg-primary text-on-primary font-label-mono text-label-mono">
          Back to the console
        </Link>
      </div>
    </main>
  );
}

export default function AuthErrorPage() {
  return (
    <Suspense fallback={null}>
      <Body />
    </Suspense>
  );
}
