"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { haversineKm } from "@/lib/geo";
import { useSession } from "@/lib/session";

/** One operated site from `GET /warehouse/facilities`. `code` is what the
 * rest of the UI sends; `id` is the warehouse integer PK. */
export type RegistryFacility = {
  id: number;
  code: string;
  name: string;
  type: string;
  operated: boolean;
  lat: number | null;
  lon: number | null;
};

export type SwitchableFacility = RegistryFacility & { distanceKm: number | null };

type FacilityContextValue = {
  facilityId: string;
  setFacilityId: (code: string) => void;
  facility: RegistryFacility;
  operatedFacilities: SwitchableFacility[];
};

const FacilityContext = createContext<FacilityContextValue | null>(null);

export function useFacility() {
  const ctx = useContext(FacilityContext);
  if (!ctx) throw new Error("useFacility must be used within a FacilityProvider");
  return ctx;
}

function withDistances(items: RegistryFacility[], origin: RegistryFacility | null): SwitchableFacility[] {
  return items.map((f) => {
    const canCompute =
      origin != null &&
      origin.lat != null &&
      origin.lon != null &&
      f.lat != null &&
      f.lon != null;
    return {
      ...f,
      distanceKm: canCompute
        ? Math.round(haversineKm(origin.lat!, origin.lon!, f.lat!, f.lon!) * 10) / 10
        : null,
    };
  });
}

export function FacilityProvider({ children }: { children: ReactNode }) {
  const { user } = useSession();
  const [items, setItems] = useState<RegistryFacility[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [facilityId, setFacilityId] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("warehouse", "/facilities?operated=true")
      .then((body: { items: RegistryFacility[] }) => {
        if (cancelled) return;
        const operated = (body.items ?? []).filter((f) => f.operated);
        if (operated.length === 0) {
          setError("No operated sites for this hospital.");
          setItems([]);
          return;
        }
        setItems(operated);
        setError(null);
        setFacilityId((current) =>
          current && operated.some((f) => f.code === current) ? current : operated[0].code,
        );
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setItems(null);
        setError(err.message || "Cannot load facilities.");
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  const facility = items?.find((f) => f.code === facilityId) ?? items?.[0] ?? null;
  const operatedFacilities = useMemo(() => withDistances(items ?? [], facility), [items, facility]);

  const value = useMemo(
    () =>
      facility && facilityId
        ? { facilityId, setFacilityId: (code: string) => setFacilityId(code), facility, operatedFacilities }
        : null,
    [facilityId, facility, operatedFacilities],
  );

  if (!user) {
    return <div className="p-4 text-sm text-muted-foreground">Loading session…</div>;
  }
  if (error && !value) {
    return (
      <div className="p-4 text-sm text-destructive">
        Cannot load facilities{error ? `: ${error}` : "."}
      </div>
    );
  }
  if (!value) {
    return <div className="p-4 text-sm text-muted-foreground">Loading sites…</div>;
  }

  return <FacilityContext.Provider value={value}>{children}</FacilityContext.Provider>;
}
