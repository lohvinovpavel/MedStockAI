"use client";

import { CopilotProvider } from "@/lib/copilot-context";
import { TopNav } from "@/components/dashboard/TopNav";
import { CopilotDrawer } from "@/components/dashboard/CopilotDrawer";

// Persistent dashboard shell: top nav + main content + AI Copilot drawer.
// Mock-data driven — see lib/mock-data.ts — no session gate here, that
// stays scoped to app/(legacy).
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <CopilotProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-muted/30 text-sm">
        <TopNav />
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
          <CopilotDrawer />
        </div>
      </div>
    </CopilotProvider>
  );
}
