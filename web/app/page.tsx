import Link from "next/link";
import {
  ArrowRight,
  Plus,
  TrendingUp,
  CheckCircle2,
  Repeat2,
  BrainCircuit,
  Sparkles,
  Database,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const METRICS = [
  {
    score: "-84%",
    label: "Expiry waste prevention",
    sublabel: "FEFO protocol optimization",
    tone: "text-[#0f77ff]",
  },
  {
    score: "0",
    label: "Critical stockout incidents",
    sublabel: "Across monitored clinical wards",
    tone: "text-[#0f77ff]",
  },
  {
    score: "<2m",
    label: "Automated PO dispatch",
    sublabel: "Forecast to supplier handoff",
    tone: "text-[#0f77ff]",
  },
  {
    score: "100%",
    label: "FDA/EMA listing verified",
    sublabel: "Real-time compliance validation",
    tone: "text-[#0f77ff]",
  },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-[#ffffff] text-[#091135] font-sans antialiased">
      {/* Top Navigation Bar — Floating on white canvas */}
      <header className="sticky top-0 z-40 flex h-16 w-full items-center justify-between border-b border-[#e1e9f0] bg-[#ffffff]/90 px-6 backdrop-blur-md sm:px-12">
        <div className="flex items-center gap-3">
          <span className="flex size-8 items-center justify-center rounded-lg bg-[#127ee3] text-white shadow-none">
            <Plus className="size-4.5" strokeWidth={3} />
          </span>
          <div className="flex flex-col">
            <span className="text-sm font-semibold tracking-heading-sm text-[#091135]">MedStock AI</span>
            <span className="text-[10px] font-medium tracking-wider uppercase text-[#36394a]">Clinical Data Observatory</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm" className="h-8 rounded-lg border-[#e1e9f0] text-xs font-medium text-[#091135] hover:bg-[#f5f3ff]">
            <Link href="/login">Demo Accounts</Link>
          </Button>
          <Button asChild size="sm" className="h-8 rounded-lg bg-[#127ee3] text-xs font-medium text-white hover:bg-[#0f77ff] focus-visible:ring-1 focus-visible:ring-[#0f77ff]">
            <Link href="/login">
              Launch Console
              <ArrowRight className="ml-1 size-3.5" />
            </Link>
          </Button>
        </div>
      </header>

      <main className="flex-1">
        {/* Hero Section — Centered opening statement on white linen canvas */}
        <section className="mx-auto flex max-w-5xl flex-col items-center gap-6 px-6 pt-20 pb-16 text-center sm:pt-28 sm:pb-20">
          {/* Eyebrow / Pill Badge in Lavender Wash */}
          <div className="inline-flex items-center gap-2 rounded-full border border-[#e1e9f0] bg-[#f5f3ff] px-4 py-1 text-xs font-medium tracking-[0.004em] text-[#091135]">
            <span className="size-1.5 rounded-full bg-[#0f77ff]" />
            <span>ISO-27001 Audited · Real-Time openFDA / RxNorm Verification</span>
          </div>

          {/* Headline in Midnight Ink with widened positive tracking */}
          <h1 className="max-w-4xl text-4xl font-semibold tracking-display text-[#091135] sm:text-6xl sm:leading-[1.15]">
            Data observatory on cloud paper for mission-critical pharma inventory
          </h1>

          {/* Subhead in Slate */}
          <p className="max-w-2xl text-base tracking-subheading leading-relaxed text-[#36394a] sm:text-lg">
            MedStock AI prevents clinical shelf stockouts before they happen — deterministic bio-equivalent lookups, Prophet ML restocking, and FDA-certified supply transfers in one near-achromatic console.
          </p>

          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg" className="h-11 rounded-lg bg-[#127ee3] px-6 text-sm font-medium text-white hover:bg-[#0f77ff] focus-visible:ring-1 focus-visible:ring-[#0f77ff]">
              <Link href="/login">
                Open Clinical Workspace
                <ArrowRight className="ml-2 size-4" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg" className="h-11 rounded-lg border-[#e1e9f0] bg-[#ffffff] px-6 text-sm font-medium text-[#091135] hover:bg-[#f5f3ff]">
              <Link href="/inventory">Explore Live Formulary</Link>
            </Button>
          </div>
        </section>

        {/* Hero Product Showcase — Floating UI mockups suspended on Lavender Wash */}
        <section className="border-y border-[#e1e9f0] bg-[#f5f3ff] py-16">
          <div className="mx-auto max-w-6xl px-6">
            <div className="mb-8 text-center">
              <span className="text-xs font-medium uppercase tracking-wider text-[#36394a]">Documentary UI Hero</span>
              <h2 className="mt-1 text-2xl font-semibold tracking-heading-sm text-[#091135]">Live Clinical Inventory &amp; Equivalence Matrix</h2>
            </div>

            {/* Suspended Product Cards Grid */}
            <div className="grid gap-6 lg:grid-cols-12 items-start">
              {/* Main Formulary Record Card (PayPay / Clearbit Style) */}
              <div className="lg:col-span-7 rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-6 shadow-none">
                <div className="flex items-start justify-between border-b border-[#e1e9f0] pb-4">
                  <div className="flex items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-lg border border-[#e1e9f0] bg-[#f5f3ff] text-[#0f77ff]">
                      <Database className="size-5" />
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-base text-[#091135]">Amoxicillin 500mg Capsule</h3>
                        <Badge variant="outline" className="border-[#e1e9f0] bg-[#f5f3ff] text-[10px] text-[#091135]">NDC 00093-3109-01</Badge>
                      </div>
                      <p className="text-xs text-[#36394a]">Tier 1 Critical Antibiotic · St. Mary&apos;s General Hospital</p>
                    </div>
                  </div>

                  {/* Circular Score Badge per DESIGN.md */}
                  <div className="flex flex-col items-center">
                    <div className="flex size-10 items-center justify-center rounded-full border border-[#e1e9f0] bg-[#ffffff] font-sans text-sm font-bold text-[#0f77ff]">
                      98
                    </div>
                    <span className="mt-0.5 text-[9px] font-medium text-[#36394a]">Match Score</span>
                  </div>
                </div>

                {/* Structured Two-Column Data Record Rows */}
                <div className="mt-5 space-y-3">
                  <div className="flex items-center justify-between text-sm py-1 border-b border-[#f5f3ff]">
                    <span className="flex items-center gap-2 text-xs text-[#36394a]">
                      <CheckCircle2 className="size-3.5 text-[#0f77ff]" /> Active Ingredient &amp; Salt
                    </span>
                    <span className="font-medium text-xs text-[#091135]">Amoxicillin Trihydrate</span>
                  </div>

                  <div className="flex items-center justify-between text-sm py-1 border-b border-[#f5f3ff]">
                    <span className="flex items-center gap-2 text-xs text-[#36394a]">
                      <CheckCircle2 className="size-3.5 text-[#0f77ff]" /> Dosage Form &amp; Route
                    </span>
                    <span className="font-medium text-xs text-[#091135]">Oral Capsule · 500 mg</span>
                  </div>

                  <div className="flex items-center justify-between text-sm py-1 border-b border-[#f5f3ff]">
                    <span className="flex items-center gap-2 text-xs text-[#36394a]">
                      <CheckCircle2 className="size-3.5 text-[#0f77ff]" /> openFDA Listing Status
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
                      <span className="size-1 rounded-full bg-emerald-500" /> Active Marketing (No Recalls)
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-sm py-1 border-b border-[#f5f3ff]">
                    <span className="flex items-center gap-2 text-xs text-[#36394a]">
                      <CheckCircle2 className="size-3.5 text-[#0f77ff]" /> On-Hand Stock &amp; Depletion
                    </span>
                    <span className="font-medium text-xs text-[#091135]">420 units · 18 days burn rate</span>
                  </div>

                  <div className="flex items-center justify-between text-sm py-1">
                    <span className="flex items-center gap-2 text-xs text-[#36394a]">
                      <CheckCircle2 className="size-3.5 text-[#0f77ff]" /> ML Forecast Recommendation
                    </span>
                    <span className="font-medium text-xs text-[#127ee3]">Trigger +250 unit replenishment</span>
                  </div>
                </div>

                <div className="mt-5 flex items-center justify-between rounded-lg border border-[#e1e9f0] bg-[#f5f3ff] p-3">
                  <div className="flex items-center gap-2">
                    <Sparkles className="size-4 text-[#0f77ff]" />
                    <span className="text-xs font-medium text-[#091135]">Bio-equivalent analogue verified</span>
                  </div>
                  <span className="text-xs font-medium text-[#0f77ff]">Ampicillin / Augmentin ready</span>
                </div>
              </div>

              {/* Secondary Floating Card: Restock & Transfer Signal */}
              <div className="lg:col-span-5 space-y-4">
                <div className="rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-5 shadow-none">
                  <div className="flex items-center justify-between pb-3 border-b border-[#e1e9f0]">
                    <div className="flex items-center gap-2">
                      <Repeat2 className="size-4 text-[#0f77ff]" />
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-[#091135]">Regional Transfer Node</h4>
                    </div>
                    <Badge variant="outline" className="border-[#e1e9f0] bg-[#f5f3ff] text-[10px] text-[#091135]">Live Network</Badge>
                  </div>

                  <div className="mt-3 space-y-2.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-[#36394a]">North Memorial Hospital</span>
                      <span className="font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded-full text-[10px]">+620 units (Surplus)</span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-[#36394a]">St. Mary&apos;s General (This Site)</span>
                      <span className="font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded-full text-[10px]">Reorder threshold reached</span>
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-[#36394a]">Estimated Dispatch Transit</span>
                      <span className="font-medium text-[#091135]">28 mins via Medical Courier</span>
                    </div>
                  </div>

                  <Button asChild size="sm" className="mt-4 w-full rounded-lg bg-[#127ee3] text-xs font-medium text-white hover:bg-[#0f77ff]">
                    <Link href="/shortages">View Shortage Matrix</Link>
                  </Button>
                </div>

                <div className="rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-5 shadow-none">
                  <div className="flex items-center gap-2 pb-2">
                    <ShieldCheck className="size-4 text-[#0f77ff]" />
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-[#091135]">FDA 21 CFR § 211 Compliance</h4>
                  </div>
                  <p className="text-xs text-[#36394a] leading-relaxed">
                    Cryptographic audit trail with SHA-256 hash chaining on every prescription, analogue substitution, and batch transfer.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Metrics Grid — 4 Restrained Blueprint Tiles */}
        <section className="mx-auto max-w-6xl px-6 py-16">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {METRICS.map(({ score, label, sublabel }) => (
              <div key={label} className="rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-5 shadow-none transition-colors hover:border-[#b1bbcd]">
                <p className="text-3xl font-semibold tracking-tight text-[#0f77ff]">{score}</p>
                <p className="mt-2 text-xs font-medium text-[#091135]">{label}</p>
                <p className="mt-1 text-[11px] text-[#36394a]">{sublabel}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Alternating Z-Pattern Features Sections */}
        {/* Feature 1: Deterministic Substitutes (Text Left / Product Card Right) */}
        <section className="border-t border-[#e1e9f0] bg-[#ffffff] py-16">
          <div className="mx-auto max-w-6xl px-6 grid gap-12 lg:grid-cols-2 items-center">
            <div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#e1e9f0] bg-[#f5f3ff] px-3 py-0.5 text-xs font-medium text-[#091135]">
                <Repeat2 className="size-3.5 text-[#0f77ff]" />
                RxNorm Active Ingredient Resolution
              </div>
              <h2 className="mt-4 text-3xl font-semibold tracking-heading-sm text-[#091135]">
                Deterministic Bio-Equivalent Lookups
              </h2>
              <p className="mt-3 text-sm tracking-body leading-relaxed text-[#36394a]">
                When a critical medication faces stockout, MedStock AI calculates therapeutically equivalent alternatives backed by RxNorm salt forms, route specifications, and openFDA marketing authorizations.
              </p>
              <ul className="mt-5 space-y-2.5 text-xs text-[#36394a]">
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-[#0f77ff]" />
                  <span>Exact molecular and dosage matching with real-time stock correlation</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-[#0f77ff]" />
                  <span>Interactive physician review gate with clinical justification logging</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-[#0f77ff]" />
                  <span>Automatic warning on active FDA recall notices and black-box alerts</span>
                </li>
              </ul>
            </div>

            <div className="rounded-xl border border-[#e1e9f0] bg-[#f5f3ff] p-6 shadow-none">
              <div className="rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-5 shadow-none">
                <div className="flex items-center justify-between border-b border-[#e1e9f0] pb-3">
                  <span className="text-xs font-semibold text-[#091135]">Substitutions for Ciprofloxacin 500mg</span>
                  <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-[10px] text-emerald-800">3 Available</Badge>
                </div>
                <div className="mt-4 space-y-3">
                  <div className="rounded-lg border border-[#e1e9f0] p-3">
                    <div className="flex justify-between items-start">
                      <div>
                        <p className="font-semibold text-xs text-[#091135]">Levofloxacin 500mg Tablet</p>
                        <p className="text-[11px] text-[#36394a]">Fluoroquinolone class · 1:1 Bio-equivalence</p>
                      </div>
                      <span className="font-semibold text-xs text-[#0f77ff]">96% Match</span>
                    </div>
                  </div>
                  <div className="rounded-lg border border-[#e1e9f0] p-3">
                    <div className="flex justify-between items-start">
                      <div>
                        <p className="font-semibold text-xs text-[#091135]">Ofloxacin 400mg Tablet</p>
                        <p className="text-[11px] text-[#36394a]">Alternative second-generation agent</p>
                      </div>
                      <span className="font-semibold text-xs text-[#0f77ff]">91% Match</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Feature 2: Predictive Restocking (Product Card Left / Text Right) */}
        <section className="border-t border-[#e1e9f0] bg-[#f5f3ff] py-16">
          <div className="mx-auto max-w-6xl px-6 grid gap-12 lg:grid-cols-2 items-center">
            <div className="order-2 lg:order-1 rounded-xl border border-[#e1e9f0] bg-[#ffffff] p-6 shadow-none">
              <div className="flex items-center justify-between border-b border-[#e1e9f0] pb-3">
                <div className="flex items-center gap-2">
                  <BrainCircuit className="size-4 text-[#0f77ff]" />
                  <span className="text-xs font-semibold text-[#091135]">Prophet Restock Forecast Model</span>
                </div>
                <Badge variant="outline" className="border-[#e1e9f0] bg-[#f5f3ff] text-[10px] text-[#091135]">95% CI</Badge>
              </div>

              <div className="mt-4 space-y-3">
                <div className="flex justify-between text-xs py-1 border-b border-[#f5f3ff]">
                  <span className="text-[#36394a]">Historical 30-Day Velocity</span>
                  <span className="font-medium text-[#091135]">14.2 units/day</span>
                </div>
                <div className="flex justify-between text-xs py-1 border-b border-[#f5f3ff]">
                  <span className="text-[#36394a]">Forecasted Seasonal Surge</span>
                  <span className="font-medium text-[#0f77ff]">+22.4% next 14 days</span>
                </div>
                <div className="flex justify-between text-xs py-1 border-b border-[#f5f3ff]">
                  <span className="text-[#36394a]">Projected Depletion Date</span>
                  <span className="font-medium text-amber-700">October 24 (6 days left)</span>
                </div>
                <div className="flex justify-between text-xs py-1">
                  <span className="text-[#36394a]">Suggested Purchase Order</span>
                  <span className="font-semibold text-[#091135]">500 units · McKesson Pharma</span>
                </div>
              </div>

              <Button asChild size="sm" className="mt-5 w-full rounded-lg bg-[#127ee3] text-xs font-medium text-white hover:bg-[#0f77ff]">
                <Link href="/forecasts">Generate Automated PO</Link>
              </Button>
            </div>

            <div className="order-1 lg:order-2">
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#e1e9f0] bg-[#ffffff] px-3 py-0.5 text-xs font-medium text-[#091135]">
                <TrendingUp className="size-3.5 text-[#0f77ff]" />
                Machine Learning Restock Engine
              </div>
              <h2 className="mt-4 text-3xl font-semibold tracking-heading-sm text-[#091135]">
                Prophet &amp; XGBoost Demand Forecasts
              </h2>
              <p className="mt-3 text-sm tracking-body leading-relaxed text-[#36394a]">
                Predictive burn-rate models trained per facility account for seasonal contagion surges, surgical ward schedules, and supplier lead times to eliminate stockouts before orders are placed.
              </p>
              <div className="mt-6 flex items-center gap-3">
                <Button asChild size="sm" className="rounded-lg bg-[#127ee3] text-xs font-medium text-white hover:bg-[#0f77ff]">
                  <Link href="/login">Explore Forecasting Models</Link>
                </Button>
              </div>
            </div>
          </div>
        </section>

        {/* Closing CTA */}
        <section className="mx-auto max-w-4xl px-6 py-20 text-center">
          <h2 className="text-3xl font-semibold tracking-heading-sm text-[#091135]">
            Ready to deploy on your hospital formulary?
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-sm tracking-subheading text-[#36394a]">
            Access our demo workspace with physician, pharmacist, director, and admin credentials.
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Button asChild size="lg" className="rounded-lg bg-[#127ee3] px-8 text-sm font-medium text-white hover:bg-[#0f77ff]">
              <Link href="/login">
                Launch Interactive Demo
                <ArrowRight className="ml-2 size-4" />
              </Link>
            </Button>
          </div>
        </section>
      </main>

      {/* Footer — Blueprint Hairline Border */}
      <footer className="border-t border-[#e1e9f0] bg-[#ffffff] px-6 py-8 text-center text-xs text-[#36394a]">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
          <div className="flex items-center gap-2">
            <span className="flex size-5 items-center justify-center rounded bg-[#127ee3] text-white">
              <Plus className="size-3" strokeWidth={3} />
            </span>
            <span className="font-semibold text-[#091135]">MedStock AI</span>
            <span className="text-[11px] text-[#36394a]">· Clinical Pharma Inventory</span>
          </div>

          <p className="text-[11px] text-[#36394a]">
            Authorized Clinical Personnel Only · GKE Cluster Multi-Region · ISO-27001 Certified
          </p>
        </div>
      </footer>
    </div>
  );
}
