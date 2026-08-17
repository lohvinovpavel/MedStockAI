"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Old /prescribe URL → Prescribe tab on /analogue. */
export default function PrescribeRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/analogue?tab=pryznachennia");
  }, [router]);
  return (
    <div className="p-4 text-xs text-muted-foreground">Redirecting to Prescribe…</div>
  );
}
