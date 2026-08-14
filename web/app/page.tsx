import Link from "next/link";
import { ArrowRight, Plus, ShieldCheck, TrendingUp, XCircle, Clock3, FileCheck2, Repeat2, BrainCircuit, Network } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const METRICS = [
  { icon: TrendingUp, label: "Expiry waste prevention", value: "-84%", tone: "text-emerald-600 dark:text-emerald-400", detail: "vs. manual FEFO tracking across pilot facilities" },
  { icon: XCircle, label: "Stockout incidents", value: "0 Critical", tone: "text-emerald-600 dark:text-emerald-400", detail: "critical-tier SKUs with zero stockouts this quarter" },
  { icon: Clock3, label: "Automated PO processing", value: "< 2 mins", tone: "text-primary", detail: "from forecast to dispatched purchase order" },
  { icon: FileCheck2, label: "Compliance audit readiness", value: "100% Valid", tone: "text-primary", detail: "FDA/EMA certificates verified in real time" },
];

const FEATURES = [
  {
    icon: Repeat2,
    title: "Deterministic Substitutes",
    description: "Bio-equivalent lookup backed by RxNorm and openFDA standards, so a stockout on one SKU never means a stockout on the therapy.",
  },
  {
    icon: BrainCircuit,
    title: "Prophet / XGBoost ML Forecasting",
    description: "Burn-rate and seasonal surge prediction trained per facility, with confidence intervals a pharmacist can actually act on.",
  },
  {
    icon: Network,
    title: "Cross-Facility Matrix",
    description: "A live regional shortage-redistribution network — see who has surplus and dispatch a transfer before a ward runs dry.",
  },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex h-14 items-center justify-between border-b px-4 sm:px-8">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Plus className="size-4" strokeWidth={3} />
          </span>
          <span className="text-sm font-semibold tracking-tight">MedStock AI</span>
        </div>
        <Button asChild size="sm" variant="outline" className="h-8 text-xs">
          <Link href="/login">Sign in</Link>
        </Button>
      </header>

      <main className="flex-1">
        <section className="mx-auto flex max-w-4xl flex-col items-center gap-5 px-6 py-20 text-center sm:py-28">
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            ISO-27001 audited · FDA/EMA certificate verified
          </span>
          <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-5xl">
            Mission-critical pharma inventory with real-time predictive AI &amp; FDA compliance
          </h1>
          <p className="max-w-2xl text-balance text-sm text-muted-foreground sm:text-base">
            MedStock AI keeps hospital and pharmacy shelves stocked before a shortage happens — deterministic
            bio-equivalent lookups, ML-driven restocking, and a cross-facility transfer network in one console.
          </p>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Button asChild size="lg" className="text-sm">
              <Link href="/login">
                Launch Demo / Sign In
                <ArrowRight data-icon="inline-end" />
              </Link>
            </Button>
          </div>
        </section>

        <section className="mx-auto grid max-w-5xl grid-cols-2 gap-3 px-6 pb-16 lg:grid-cols-4">
          {METRICS.map(({ icon: Icon, label, value, tone, detail }) => (
            <Card key={label} className="group gap-2 py-4 transition-shadow hover:shadow-md">
              <CardContent className="px-4">
                <Icon className={`size-5 ${tone}`} />
                <p className={`mt-3 text-2xl font-semibold tracking-tight ${tone}`}>{value}</p>
                <p className="mt-1 text-xs font-medium">{label}</p>
                <p className="mt-1 text-xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">{detail}</p>
              </CardContent>
            </Card>
          ))}
        </section>

        <section className="border-t bg-muted/30 py-16">
          <div className="mx-auto max-w-5xl px-6">
            <h2 className="text-center text-xl font-semibold tracking-tight sm:text-2xl">Built for the pharmacy back office, not a slide deck</h2>
            <div className="mt-10 grid gap-4 sm:grid-cols-3">
              {FEATURES.map(({ icon: Icon, title, description }) => (
                <Card key={title} className="gap-3 py-5">
                  <CardHeader className="px-5">
                    <span className="flex size-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <Icon className="size-4.5" />
                    </span>
                    <CardTitle className="mt-2 text-sm">{title}</CardTitle>
                    <CardDescription className="text-xs leading-relaxed">{description}</CardDescription>
                  </CardHeader>
                </Card>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto flex max-w-3xl flex-col items-center gap-4 px-6 py-16 text-center">
          <h2 className="text-xl font-semibold tracking-tight sm:text-2xl">See it on your own formulary</h2>
          <p className="max-w-xl text-sm text-muted-foreground">
            Sign in with a demo role to explore inventory, forecasts, and the shortage matrix with realistic data.
          </p>
          <Button asChild size="lg" className="text-sm">
            <Link href="/login">
              Launch Demo / Sign In
              <ArrowRight data-icon="inline-end" />
            </Link>
          </Button>
        </section>
      </main>

      <footer className="border-t px-6 py-6 text-center text-xs text-muted-foreground">
        MedStock AI — Authorized Clinical Personnel Only. All access is logged per ISO-27001.
      </footer>
    </div>
  );
}
