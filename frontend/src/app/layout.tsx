import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pashupatastra — Block Planning Command Center",
  description:
    "AI-powered maintenance block planning dashboard for Indian Railways. Optimized schedule visualization, KPIs, and explainability.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-[#0a0e17] text-[#e2e8f0]">
        {children}
      </body>
    </html>
  );
}
