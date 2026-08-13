"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";

// Owner: Tymur. Backend: services/auth (Ingress path /api/auth).
// The token is never touched here — /login sets an httpOnly cookie the
// browser attaches to every later apiFetch (docs/auth-spec.md §4).
export default function AuthPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [me, setMe] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await apiFetch("auth", "/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setMe(await apiFetch("auth", "/me"));
    } catch {
      // The backend returns one message for every failure on purpose; do not
      // try to say more here than it does.
      setError("invalid credentials");
    }
  }

  async function logout() {
    await apiFetch("auth", "/logout", { method: "POST" });
    setMe(null);
  }

  if (me) {
    return (
      <main>
        <h1>auth</h1>
        <p>
          {me.full_name} — {me.role} at {me.hospital_name}
        </p>
        <button onClick={logout}>log out</button>
      </main>
    );
  }

  return (
    <main>
      <h1>auth</h1>
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
        <button type="submit">log in</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
