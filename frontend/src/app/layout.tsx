import type { Metadata } from "next";
import "./fonts.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pashupatastra — Railway Maintenance Block Planning",
  description:
    "Decision-support console for railway maintenance possession planning: field intake, deterministic risk scoring, CP-SAT block optimization, authority review, execution evidence and audit. Offline dated public-railway-data snapshot with demo maintenance inputs; no live feed.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
