"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "@/lib/session";

// Owner: Tymur. Backend: services/auth (Ingress path /api/auth).
// The token is never touched here — /login sets an httpOnly cookie the
// browser attaches to every later apiFetch (docs/auth-spec.md §4).

// Only a same-app path is a safe redirect target — anything starting "//" or
// with a scheme is an open-redirect (e.g. //evil.com parses as protocol-
// relative). Reject those and fall back to "/".
function sanitizeNext(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/";
}

function AuthForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const { user, login } = useSession();
  const router = useRouter();
  const next = sanitizeNext(useSearchParams().get("next"));

  // Already signed in and landed on /auth anyway — send them on rather than
  // showing a login form.
  useEffect(() => {
    if (user) router.replace(next);
  }, [user, next, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setPending(true);
    try {
      await login(email, password);
      router.replace(next);
    } catch {
      // The backend returns one message for every failure on purpose (unknown
      // email, wrong password, locked, inactive) so it isn't an
      // account-existence oracle; do not try to say more here than it does.
      setError("invalid credentials");
    } finally {
      setPending(false);
    }
  }

  if (user) return null;

  return (
    <main>
      <h1>log in</h1>
      <form onSubmit={submit}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
          autoComplete="username"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoComplete="current-password"
          required
        />
        <button type="submit" disabled={pending}>
          {pending ? "logging in…" : "log in"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}

export default function AuthPage() {
  // useSearchParams requires a Suspense boundary in the App Router.
  return (
    <Suspense fallback={null}>
      <AuthForm />
    </Suspense>
  );
}
