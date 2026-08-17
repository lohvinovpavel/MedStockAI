"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { facilityById, operatedFacilities, type Facility } from "@/lib/mock-data";

// The facility the user is currently operating as. Inventory, analogue
// availability, shortage labelling and the default order facility all read
// from here — switching site in the sidebar re-scopes the whole dashboard.
type FacilityContextValue = {
  facilityId: string;
  setFacilityId: (id: string) => void;
  facility: Facility;
};

const FacilityContext = createContext<FacilityContextValue | null>(null);

export function useFacility() {
  const ctx = useContext(FacilityContext);
  if (!ctx) throw new Error("useFacility must be used within a FacilityProvider");
  return ctx;
}

export function FacilityProvider({ children }: { children: ReactNode }) {
  const [facilityId, setFacilityId] = useState(operatedFacilities[0].id);
  const value = useMemo(
    () => ({ facilityId, setFacilityId, facility: facilityById(facilityId) }),
    [facilityId],
  );

  return <FacilityContext.Provider value={value}>{children}</FacilityContext.Provider>;
}
