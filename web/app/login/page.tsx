"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Plus, ShieldCheck, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import { sanitizeNextPath, SessionProvider } from "@/lib/session";

// Reads ?next= directly from location.search rather than useSearchParams() —
// that hook forces a Suspense boundary that never resumes on a direct load
// of this already-"use client" route.
function nextDestination(): string {
  const rawNext = new URLSearchParams(window.location.search).get("next");
  return rawNext ? sanitizeNextPath(rawNext) : "/inventory";
}

function LoginForm() {
  const router = useRouter();
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

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-muted/40 p-4">
      <div className="mb-6 flex items-center gap-2">
        <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-4.5" strokeWidth={3} />
        </span>
        <span className="text-base font-semibold tracking-tight">MedStock AI</span>
      </div>

      <Card className="w-full max-w-sm gap-4 py-6">
        <CardHeader className="px-6">
          <CardTitle className="text-base">Sign in to your facility</CardTitle>
          <CardDescription className="text-xs">
            Clinical staff sign-in. The session cookie is used for analogue search and prescribing.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-6">
          <form onSubmit={submitCredentials}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="login-email">Email</FieldLabel>
                <Input
                  id="login-email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@hospital.org"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="login-password">Password</FieldLabel>
                <Input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </Field>
              {error ? (
                <p className="text-xs text-destructive" role="alert">
                  {error}
                </p>
              ) : null}
              <Button type="submit" className="w-full" disabled={pending}>
                {pending && <Loader2 className="animate-spin" data-icon="inline-start" />}
                Sign in
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>

      <p className="mt-5 flex max-w-sm items-start gap-1.5 text-center text-[11px] text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
        Authorized Clinical Personnel Only. All access is logged per ISO-27001.
      </p>

      <Link href="/" className="mt-3 text-[11px] text-muted-foreground underline-offset-4 hover:underline">
        ← Back to overview
      </Link>
    </div>
  );
}

export default function LoginPage() {
  // Not nested under app/(dashboard) or app/(legacy) — this route has no
  // SessionProvider from a parent layout, so it mounts its own. redirectToAuth
  // is off: this page must never redirect itself away.
  return (
    <SessionProvider redirectToAuth={false}>
      <LoginForm />
    </SessionProvider>
  );
}
