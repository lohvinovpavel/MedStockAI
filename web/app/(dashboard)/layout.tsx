"use client";

import { useEffect, useState } from "react";
import { CopilotProvider } from "@/lib/copilot-context";
import { FacilityProvider } from "@/lib/facility-context";
import { InventoryProvider } from "@/lib/inventory-context";
import { OrdersProvider } from "@/lib/orders-context";
import { SessionProvider } from "@/lib/session";
import { useRouteGuard } from "@/lib/rbac";
import { MobileTopBar, SideNav } from "@/components/dashboard/SideNav";
import { CopilotDrawer } from "@/components/dashboard/CopilotDrawer";
import { systemStatus } from "@/lib/system-status";

// Footer's live telemetry strip — ticks the RxNorm sync clock forward once
// a minute so the footer reads as a monitored system, not a frozen timestamp.
function LiveTelemetry() {
  const [elapsedMin, setElapsedMin] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsedMin((m) => m + 1), 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <span className="flex items-center gap-2">
      <span className="flex items-center gap-1.5 uppercase tracking-wider text-emerald-400">
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-75" />
          <span className="relative inline-flex size-1.5 rounded-full bg-emerald-500" />
        </span>
        Live
      </span>
      <span className="text-neutral-700">|</span>
      <span>RxNorm Sync: {systemStatus.rxNormSyncMinutesAgo + elapsedMin}m ago</span>
      <span className="text-neutral-700">|</span>
      <span>
        GKE: <span className="text-emerald-400">healthy</span>
      </span>
    </span>
  );
}

// Blocks a page's content while useRouteGuard is mid-redirect, so a role
// denied a route never gets a frame of it before the bounce lands. The
// shell (nav, footer) stays up — only the page slot goes blank.
function RoleGate({ children }: { children: React.ReactNode }) {
  const denied = useRouteGuard();
  return denied ? null : <>{children}</>;
}

// Persistent dashboard shell: left nav + main content + AI MedStock
// Assistant drawer + status/audit footer. No header at `lg` and above —
// search lives on the Inventory page and the assistant drawer has its own
// open/collapse control.
// Below `lg`, SideNav and CopilotDrawer collapse to nothing inline (there's
// no room for two fixed side columns on a phone/tablet), so MobileTopBar
// supplies the only way to reach either — a hamburger Sheet for nav, a
// button that opens the copilot as a Sheet. Route access is cookie-gated:
// an unauthenticated visitor is bounced to /login, and a signed-in visitor
// whose role can't see this route (lib/rbac.ts) is bounced to their home page
// before any page here renders.
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider authPath="/login">
      <FacilityProvider>
      <InventoryProvider>
      <OrdersProvider>
        <CopilotProvider>
          <div className="flex h-screen flex-col overflow-hidden bg-muted/30 text-sm">
            <MobileTopBar />
            <div className="flex min-h-0 flex-1">
              <SideNav />
              <main className="min-w-0 flex-1 overflow-y-auto"><RoleGate>{children}</RoleGate></main>
              <CopilotDrawer />
            </div>
            <footer className="flex h-7 shrink-0 flex-wrap items-center gap-2 overflow-x-auto border-t bg-neutral-950 px-3 font-mono text-[10px] tracking-wide text-neutral-500">
              <LiveTelemetry />
              <span className="ml-auto flex items-center gap-2">
                <span>
                  Audit Hash: <span className="text-neutral-300">SHA256:{systemStatus.auditHash}&hellip;</span>
                </span>
                <span className="text-neutral-700">&bull;</span>
                <span>
                  Node: <span className="text-neutral-300">{systemStatus.gkeCluster}</span>
                </span>
                <span className="text-neutral-700">&bull;</span>
                <span className="text-neutral-300">{systemStatus.complianceStandard}</span>
              </span>
            </footer>
          </div>
        </CopilotProvider>
      </OrdersProvider>
      </InventoryProvider>
      </FacilityProvider>
    </SessionProvider>
  );
}
