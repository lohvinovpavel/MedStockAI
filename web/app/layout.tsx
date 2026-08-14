import type { Metadata } from "next";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

export const metadata: Metadata = {
  title: "MedStock AI",
  description: "Mission-critical pharma inventory with real-time predictive AI & FDA compliance.",
};

// Deliberately bare — session gating and the legacy nav live in
// app/(legacy)/layout.tsx, scoped to the old backend-integrated scaffold.
// The marketing site, /login, and app/(dashboard) supply their own chrome.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <TooltipProvider delayDuration={200}>
          {children}
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
