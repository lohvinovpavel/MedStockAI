import Link from "next/link";
import { SERVICES } from "@/lib/services";
import "./globals.css";

export const metadata = { title: "MedStockAI" };

// Baseline styling only (app/globals.css) — no design system, every page
// here is still a placeholder for the real UI. Nav is generated from
// SERVICES so a new entry there shows up here too.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav>
          <Link href="/">home</Link>
          {Object.keys(SERVICES).map((name) => (
            <Link key={name} href={`/${name}`}>
              {name}
            </Link>
          ))}
        </nav>
        {children}
      </body>
    </html>
  );
}
