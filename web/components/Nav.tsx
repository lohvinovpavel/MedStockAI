"use client";

import Link from "next/link";
import { SERVICES } from "@/lib/services";
import { useSession } from "@/lib/session";

// auth is a route (the login page), not a feature page — don't list it as
// a service link once the user is signed in.
const NAV_SERVICES = Object.keys(SERVICES).filter((name) => name !== "auth");

export default function Nav() {
  const { user, logout } = useSession();

  return (
    <nav>
      <Link href="/">home</Link>
      {user !== null &&
        NAV_SERVICES.map((name) => (
          <Link key={name} href={`/${name}`}>
            {name}
          </Link>
        ))}
      <Link href="/version">version</Link>
      {user && (
        <span className="nav-user">
          <span className="muted">
            {user.full_name ?? user.email} — {user.role} at {user.hospital_name}
          </span>
          <button className="secondary" onClick={() => logout()}>
            log out
          </button>
        </span>
      )}
    </nav>
  );
}
