"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Old /prescribe URL → Призначення tab on /analogue. */
export default function PrescribeRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/analogue?tab=pryznachennia");
  }, [router]);
  return (
    <main>
      <p className="muted">Redirecting to Призначення…</p>
    </main>
  );
}
