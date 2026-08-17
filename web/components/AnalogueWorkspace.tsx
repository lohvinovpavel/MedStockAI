"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Repeat2, Stethoscope } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          {tab === "pryznachennia" ? "Призначення" : "Analogues"}
        </h1>
        <p className="text-xs text-muted-foreground">
          {tab === "pryznachennia"
            ? "Search drugs, check a patient profile, and generate a prescription summary. Cart lives in this browser tab only."
            : "Search by name, confirm a preparation to see shelf status, then find analogues ranked in-stock first. No patient profile is involved."}
        </p>
      </div>

      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (value === "analogues" || (value === "pryznachennia" && canPrescribe)) {
            setTab(value);
          }
        }}
      >
        <TabsList variant="line">
          <TabsTrigger value="analogues">
            <Repeat2 /> Пошук аналогів
          </TabsTrigger>
          {canPrescribe && (
            <TabsTrigger value="pryznachennia">
              <Stethoscope /> Призначення
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="analogues" className="flex flex-col gap-3">
          <ServiceHealth service="analogue" />
          <DrugSearch />
        </TabsContent>

        {canPrescribe && (
          <TabsContent value="pryznachennia">
            <PrescriptionCart />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
