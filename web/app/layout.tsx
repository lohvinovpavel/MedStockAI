import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

// Feeds Tailwind's `--font-sans` (see globals.css `@theme inline`) — was
// unset, so `font-sans` fell back to the browser's generic system stack.
// Manrope's geometric-but-warm feel reads as modern health-tech rather than
// generic SaaS default; `.legacy` scaffold keeps its own system-ui stack
// untouched (scoped separately, see globals.css).
const manrope = Manrope({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "MedStock AI",
  description: "Mission-critical pharma inventory with real-time predictive AI & FDA compliance.",
};

// Deliberately bare — session gating and the legacy nav live in
// app/(legacy)/layout.tsx, scoped to the old backend-integrated scaffold.
// The marketing site, /login, and app/(dashboard) supply their own chrome.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={manrope.variable}>
      <body>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <TooltipProvider delayDuration={200}>
            {children}
            <Toaster />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
