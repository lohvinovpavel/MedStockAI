"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Plus, ShieldCheck, ShieldOff, Stethoscope, ClipboardList, UserCog, Truck, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel, FieldSeparator } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import { sanitizeNextPath, SessionProvider, useSession, LOCAL_AUTH_ENABLED } from "@/lib/session";
import { ROLE_LABEL } from "@/lib/rbac";

// One seeded account per role (services/auth/app/seed.py) — all four the
// app actually has, so a reviewer can sign in as each without knowing the
// seed script exists.
const DEMO_ROLES = [
  { role: "physician", name: "Ben Okafor", email: "ben@stmarys.org", icon: Stethoscope },
  { role: "pharmacist", name: "Ann Reyes", email: "ann@stmarys.org", icon: ClipboardList },
  { role: "director", name: "Cara Lindqvist", email: "cara@stmarys.org", icon: UserCog },
  { role: "admin", name: "Dan Whitfield", email: "dan@stmarys.org", icon: Truck },
] as const;

// Reads ?next= directly from location.search rather than useSearchParams() —
// that hook forces a Suspense boundary that never resumes on a direct load
// of this already-"use client" route.
function nextDestination(): string {
  const rawNext = new URLSearchParams(window.location.search).get("next");
  return rawNext ? sanitizeNextPath(rawNext) : "/inventory";
}

function LoginForm() {
  const router = useRouter();
  const { loginLocal } = useSession();
  const [pending, setPending] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function signIn(nextEmail: string, nextPassword: string) {
    setError("");
    setPending(true);
    try {
      await apiFetch("auth", "/login", {
        method: "POST",
        body: JSON.stringify({ email: nextEmail, password: nextPassword }),
      });
      toast.success("Signed in.");
      router.push(nextDestination());
    } catch {
      setError("invalid credentials");
      setPending(false);
    }
  }

  function submitCredentials(e: React.FormEvent) {
    e.preventDefault();
    void signIn(email, password);
  }

  // No password, no backend call — see LOCAL_AUTH_ENABLED. Only reachable
  // when that flag was set at build time, which is why the section below
  // doesn't render at all otherwise.
  function signInLocal(role: string, demoEmail: string, name: string) {
    loginLocal(role, demoEmail, name);
    toast.success("Signed in (local, no backend).");
    router.push(nextDestination());
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[#f5f3ff] p-4 text-[#091135]">
      <div className="mb-6 flex items-center gap-2.5">
        <span className="flex size-9 items-center justify-center rounded-lg bg-[#127ee3] text-white shadow-none">
          <Plus className="size-5" strokeWidth={3} />
        </span>
        <div className="flex flex-col">
          <span className="text-base font-semibold tracking-tight text-[#091135]">MedStock AI</span>
          <span className="text-[10px] font-medium uppercase tracking-wider text-[#36394a]">Clinical Data Observatory</span>
        </div>
      </div>

      <Card className="w-full max-w-sm gap-4 py-6 border-[#e1e9f0] bg-[#ffffff] shadow-none">
        <CardHeader className="px-6 pb-2">
          <CardTitle className="text-base font-semibold text-[#091135] tracking-[0.008em]">Sign in to your facility</CardTitle>
          <CardDescription className="text-xs text-[#36394a] leading-relaxed">
            Clinical staff authentication. Session tokens are verified against facility role boundaries.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-6">
          <form onSubmit={submitCredentials}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="login-email" className="text-xs font-medium text-[#091135]">Email Address</FieldLabel>
                <Input
                  id="login-email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@hospital.org"
                  className="rounded-lg border-[#e1e9f0] bg-[#ffffff] text-sm focus-visible:ring-1 focus-visible:ring-[#0f77ff]"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="login-password" className="text-xs font-medium text-[#091135]">Password</FieldLabel>
                <Input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="rounded-lg border-[#e1e9f0] bg-[#ffffff] text-sm focus-visible:ring-1 focus-visible:ring-[#0f77ff]"
                />
              </Field>
              {error ? (
                <p className="text-xs text-red-600 font-medium" role="alert">
                  {error}
                </p>
              ) : null}
              <Button type="submit" className="w-full rounded-lg bg-[#127ee3] text-sm font-medium text-white hover:bg-[#0f77ff] focus-visible:ring-1 focus-visible:ring-[#0f77ff]" disabled={pending}>
                {pending && <Loader2 className="animate-spin mr-1.5 size-4" />}
                Sign in to Console
              </Button>
            </FieldGroup>
          </form>

          <FieldSeparator className="my-4 text-xs text-[#36394a]">or select seeded role</FieldSeparator>

          <div className="flex flex-col gap-2">
            {DEMO_ROLES.map(({ role, email: demoEmail, icon: Icon }) => (
              <Button
                key={role}
                type="button"
                variant="outline"
                className="w-full justify-start rounded-lg border-[#e1e9f0] bg-[#ffffff] text-xs font-normal text-[#091135] hover:bg-[#f5f3ff] hover:text-[#091135]"
                disabled={pending}
                onClick={() => setEmail(demoEmail)}
              >
                <Icon className="mr-2 size-3.5 text-[#0f77ff]" />
                Use {ROLE_LABEL[role]} ({demoEmail})
              </Button>
            ))}
          </div>

          {LOCAL_AUTH_ENABLED && (
            <>
              <FieldSeparator className="my-4 text-xs text-[#36394a]">or bypass local dev</FieldSeparator>
              <div className="flex flex-col gap-2">
                {DEMO_ROLES.map(({ role, name, email: demoEmail, icon: Icon }) => (
                  <Button
                    key={role}
                    type="button"
                    variant="secondary"
                    className="w-full justify-start rounded-lg border border-[#e1e9f0] bg-[#f5f3ff] text-xs font-normal text-[#091135] hover:bg-[#e1e9f0]"
                    onClick={() => signInLocal(role, demoEmail, name)}
                  >
                    <Icon className="mr-2 size-3.5 text-[#0f77ff]" />
                    Continue as {name} — no password
                  </Button>
                ))}
                <p className="flex items-start gap-1.5 text-[11px] text-[#36394a]">
                  <ShieldOff className="mt-0.5 size-3.5 shrink-0 text-[#36394a]" />
                  Local dev mode — client session active.
                </p>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <p className="mt-5 flex max-w-sm items-start gap-1.5 text-center text-[11px] text-[#36394a]">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-[#0f77ff]" />
        Authorized Clinical Personnel Only. All access is logged per ISO-27001.
      </p>

      <Link href="/" className="mt-3 text-[11px] text-[#36394a] underline-offset-4 hover:underline hover:text-[#091135]">
        ← Back to overview
      </Link>
    </div>
  );
}

export default function LoginPage() {
  // Not nested under app/(dashboard) or app/(legacy) — this route has no
  // SessionProvider from a parent layout, so it mounts its own just to
  // reach loginLocal()/useSession(). redirectToAuth is off: this page must
  // never redirect itself away.
  return (
    <SessionProvider redirectToAuth={false}>
      <LoginForm />
    </SessionProvider>
  );
}
