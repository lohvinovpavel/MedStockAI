import { SessionProvider } from "@/lib/session";
import Nav from "@/components/Nav";

// The original bare-bones service scaffold (real backend calls, auth-gated).
// Kept reachable at its original URLs for backend testing; the dashboard
// under app/(dashboard) is the primary UI now and does not need this session gate.
export default function LegacyLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="legacy">
      <SessionProvider>
        <Nav />
        {children}
      </SessionProvider>
    </div>
  );
}
