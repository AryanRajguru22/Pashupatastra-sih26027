import type { ReactNode } from "react";
import { SessionProvider } from "@/lib/session";
import Shell from "@/components/Shell";
import { ToastProvider } from "@/components/ui";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <ToastProvider>
        <Shell>{children}</Shell>
      </ToastProvider>
    </SessionProvider>
  );
}
