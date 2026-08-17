"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { DrugSearch } from "@/components/DrugSearch";
import { PrescriptionCart } from "@/components/PrescriptionCart";
import { ServiceHealth } from "@/components/ServiceHealth";
import { useSession } from "@/lib/session";

type TabId = "analogues" | "pryznachennia";

export function AnalogueWorkspace() {
  const { user } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const canPrescribe = user?.role === "physician" || user?.role === "admin";

  const tab: TabId = useMemo(() => {
    const raw = searchParams.get("tab");
    if (raw === "pryznachennia" && canPrescribe) return "pryznachennia";
    return "analogues";
  }, [searchParams, canPrescribe]);

  const setTab = useCallback(
    (next: TabId) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === "analogues") params.delete("tab");
      else params.set("tab", next);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  return (
    <div className="analogue-workspace">
      <div className="tab-bar" role="tablist" aria-label="Analogue workspace">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "analogues"}
          className={tab === "analogues" ? "tab active" : "tab"}
          onClick={() => setTab("analogues")}
        >
          Пошук аналогів
        </button>
        {canPrescribe && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "pryznachennia"}
            className={tab === "pryznachennia" ? "tab active" : "tab"}
            onClick={() => setTab("pryznachennia")}
          >
            Призначення
          </button>
        )}
      </div>

      {tab === "analogues" ? (
        <div role="tabpanel">
          <h1>Пошук аналогів</h1>
          <p>
            Search by name, confirm a preparation to see its shelf status, then
            find analogues ranked in-stock first with High / Normal / Low / Out
            of stock. No patient profile is involved.
          </p>
          <ServiceHealth service="analogue" />
          <DrugSearch />
        </div>
      ) : (
        <div role="tabpanel">
          <h1>Призначення</h1>
          <PrescriptionCart />
        </div>
      )}
    </div>
  );
}
