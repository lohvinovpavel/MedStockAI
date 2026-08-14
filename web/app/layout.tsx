import { SessionProvider } from "@/lib/session";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata = { title: "MedStockAI" };

// Baseline styling only (app/globals.css) — no design system, every page
// here is still a placeholder for the real UI. Nav is generated from
// SERVICES so a new entry there shows up here too. Stays a server component;
// SessionProvider/Nav are the client boundary.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <Nav />
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}
