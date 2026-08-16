// Mock data for the MedStock AI dashboard demo. Frontend-only — nothing
// here hits a real backend. Dates are generated relative to `today` so the
// dashboard always looks current regardless of when it's viewed.

export const today = new Date("2026-08-14T00:00:00Z");

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function daysUntil(dateIso: string): number {
  const diff = new Date(dateIso).getTime() - today.getTime();
  return Math.round(diff / 86_400_000);
}

// Date N days out from the mock "now", for lead times and delivery ETAs.
export function isoPlusDays(days: number): string {
  return iso(addDays(today, days));
}

// ---------------------------------------------------------------------
// Facility network
// ---------------------------------------------------------------------

export interface Facility {
  id: string;
  name: string;
  type: "Hospital" | "Clinic" | "Pharmacy" | "Warehouse";
  distanceKm: number; // from Central Hospital
  operated: boolean; // true = we hold stock here and can switch to it
}

// Single source of truth for facility names across the app. Sites we
// operate can be selected in the sidebar switcher; partner sites are
// visible in the shortage matrix and analogue lookups but not operable.
export const facilities: Facility[] = [
  { id: "fac-central", name: "Central Hospital", type: "Hospital", distanceKm: 0, operated: true },
  { id: "fac-riverside", name: "Riverside Outpatient", type: "Clinic", distanceKm: 19, operated: true },
  { id: "fac-westend", name: "West End Community", type: "Clinic", distanceKm: 41, operated: true },
  { id: "fac-warehouse-n", name: "Regional Warehouse North", type: "Warehouse", distanceKm: 34, operated: true },
  { id: "fac-stluke", name: "St. Luke Hospital", type: "Hospital", distanceKm: 12, operated: false },
  { id: "fac-mercy", name: "Mercy Pharmacy Network", type: "Pharmacy", distanceKm: 27, operated: false },
];

export const operatedFacilities = facilities.filter((f) => f.operated);

export function facilityById(id: string): Facility {
  return facilities.find((f) => f.id === id) ?? facilities[0];
}

// ---------------------------------------------------------------------
// Inventory & Batches
// ---------------------------------------------------------------------

export type StockRisk = "critical" | "warning" | "normal";
export type CertStatus = "valid" | "pending" | "expired";

export type AnalogueEquivalence = "bioequivalent" | "therapeutic" | "same-class";
export type AnalogueSource = "RxNorm" | "ATC/WHO" | "OpenFDA";

// Shaped like a response from an open drug-terminology API (RxNorm et al):
// each candidate carries its own provenance and a similarity score, and
// stock is reported per facility so the UI can answer "do we have it here?".
export interface AnalogueOption {
  id: string;
  drugName: string;
  inn: string;
  unit: string;
  rxcui: string;
  matchScore: number; // 0-100, sorted descending = best matches first
  equivalence: AnalogueEquivalence;
  source: AnalogueSource;
  stockByFacility: Record<string, number>;
}

export interface InventoryItem {
  id: string;
  facilityId: string;
  drugName: string;
  form: string;
  inn: string;
  atcCode: string;
  batchNumber: string;
  currentStock: number;
  unit: string;
  dailyBurnRate: number;
  expiryDate: string;
  certStatus: CertStatus;
  certAuthority: "FDA" | "EMA";
  certNumber: string;
  analogues: AnalogueOption[];
}

function daysOfSupply(item: Pick<InventoryItem, "currentStock" | "dailyBurnRate">): number {
  return item.dailyBurnRate > 0 ? Math.floor(item.currentStock / item.dailyBurnRate) : Infinity;
}

// Graded against the item's own reorder point (burn rate × its supplier's
// lead time, plus a safety buffer) rather than a flat day count — a SKU
// with an 11-day lead time is at risk earlier than one with a 3-day lead
// time, even at identical days-of-supply. See reorderPoint() below.
export function stockRisk(item: Pick<InventoryItem, "id" | "currentStock" | "dailyBurnRate">): StockRisk {
  const rp = reorderPoint(item);
  if (item.currentStock <= rp * 0.5) return "critical";
  if (item.currentStock <= rp) return "warning";
  return "normal";
}

export { daysOfSupply };

export const inventory: InventoryItem[] = [
  {
    id: "inv-001",
    facilityId: "fac-central",
    drugName: "Amoxicillin/Clavulanate 875mg",
    form: "Film-coated tablet",
    inn: "Amoxicillin, Clavulanic acid",
    atcCode: "J01CR02",
    batchNumber: "AMX-24118-B",
    currentStock: 900,
    unit: "boxes",
    dailyBurnRate: 62,
    expiryDate: iso(addDays(today, 21)),
    certStatus: "valid",
    certAuthority: "FDA",
    certNumber: "NDA-050760-A2",
    analogues: [
      { id: "an-01", drugName: "Co-Amoxiclav 875/125mg", inn: "Amoxicillin, Clavulanic acid", unit: "boxes", rxcui: "562251", matchScore: 98, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 340, "fac-riverside": 0, "fac-westend": 48, "fac-warehouse-n": 1200, "fac-stluke": 210, "fac-mercy": 64 } },
      { id: "an-02", drugName: "Augmentin 875mg", inn: "Amoxicillin, Clavulanic acid", unit: "boxes", rxcui: "562508", matchScore: 96, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 96, "fac-riverside": 30, "fac-westend": 0, "fac-warehouse-n": 640, "fac-stluke": 0, "fac-mercy": 120 } },
      { id: "an-03", drugName: "Amoxicillin 875mg (no clavulanate)", inn: "Amoxicillin", unit: "boxes", rxcui: "308191", matchScore: 74, equivalence: "therapeutic", source: "ATC/WHO", stockByFacility: { "fac-central": 220, "fac-riverside": 85, "fac-westend": 60, "fac-warehouse-n": 900, "fac-stluke": 40, "fac-mercy": 0 } },
      { id: "an-04", drugName: "Cefuroxime 500mg", inn: "Cefuroxime axetil", unit: "boxes", rxcui: "309089", matchScore: 61, equivalence: "same-class", source: "ATC/WHO", stockByFacility: { "fac-central": 0, "fac-riverside": 0, "fac-westend": 25, "fac-warehouse-n": 310, "fac-stluke": 90, "fac-mercy": 44 } },
    ],
  },
  {
    id: "inv-002",
    facilityId: "fac-central",
    drugName: "Propofol 1% Emulsion",
    form: "IV emulsion, 20mL ampoule",
    inn: "Propofol",
    atcCode: "N01AX10",
    batchNumber: "PPF-24902-C",
    currentStock: 250,
    unit: "ampoules",
    dailyBurnRate: 18,
    expiryDate: iso(addDays(today, 65)),
    certStatus: "valid",
    certAuthority: "EMA",
    certNumber: "EU/1/19/1156",
    analogues: [
      { id: "an-05", drugName: "Diprivan 1%", inn: "Propofol", unit: "ampoules", rxcui: "203155", matchScore: 99, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 60, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 480, "fac-stluke": 120, "fac-mercy": 0 } },
      { id: "an-06", drugName: "Propofol-Lipuro 1%", inn: "Propofol", unit: "ampoules", rxcui: "1010600", matchScore: 94, equivalence: "bioequivalent", source: "OpenFDA", stockByFacility: { "fac-central": 0, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 260, "fac-stluke": 75, "fac-mercy": 0 } },
      { id: "an-07", drugName: "Etomidate 2mg/mL", inn: "Etomidate", unit: "ampoules", rxcui: "310798", matchScore: 58, equivalence: "same-class", source: "ATC/WHO", stockByFacility: { "fac-central": 40, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 150, "fac-stluke": 30, "fac-mercy": 0 } },
    ],
  },
  {
    id: "inv-003",
    facilityId: "fac-central",
    drugName: "Ceftriaxone 1g",
    form: "Powder for injection, vial",
    inn: "Ceftriaxone sodium",
    atcCode: "J01DD04",
    batchNumber: "CFX-25011-A",
    currentStock: 9,
    unit: "vials",
    dailyBurnRate: 6,
    expiryDate: iso(addDays(today, 8)),
    certStatus: "pending",
    certAuthority: "FDA",
    certNumber: "ANDA-065432 (renewal filed)",
    analogues: [
      { id: "an-08", drugName: "Rocephin 1g", inn: "Ceftriaxone sodium", unit: "vials", rxcui: "309090", matchScore: 99, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 4, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 180, "fac-stluke": 210, "fac-mercy": 35 } },
      { id: "an-09", drugName: "Ceftriaxone 1g (generic)", inn: "Ceftriaxone sodium", unit: "vials", rxcui: "1665088", matchScore: 97, equivalence: "bioequivalent", source: "OpenFDA", stockByFacility: { "fac-central": 0, "fac-riverside": 22, "fac-westend": 0, "fac-warehouse-n": 540, "fac-stluke": 160, "fac-mercy": 90 } },
      { id: "an-10", drugName: "Cefotaxime 1g", inn: "Cefotaxime sodium", unit: "vials", rxcui: "309073", matchScore: 82, equivalence: "therapeutic", source: "ATC/WHO", stockByFacility: { "fac-central": 65, "fac-riverside": 0, "fac-westend": 18, "fac-warehouse-n": 300, "fac-stluke": 0, "fac-mercy": 0 } },
      { id: "an-11", drugName: "Cefepime 1g", inn: "Cefepime hydrochloride", unit: "vials", rxcui: "309062", matchScore: 68, equivalence: "same-class", source: "ATC/WHO", stockByFacility: { "fac-central": 0, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 120, "fac-stluke": 55, "fac-mercy": 0 } },
    ],
  },
  {
    id: "inv-004",
    facilityId: "fac-central",
    drugName: "Salbutamol 100mcg Inhaler",
    form: "Pressurized MDI",
    inn: "Salbutamol sulfate",
    atcCode: "R03AC02",
    batchNumber: "SLB-24775-D",
    currentStock: 310,
    unit: "inhalers",
    dailyBurnRate: 14,
    expiryDate: iso(addDays(today, 340)),
    certStatus: "valid",
    certAuthority: "FDA",
    certNumber: "NDA-020983-C1",
    analogues: [],
  },
  {
    id: "inv-005",
    facilityId: "fac-central",
    drugName: "Norepinephrine 4mg/4mL",
    form: "IV concentrate, ampoule",
    inn: "Norepinephrine bitartrate",
    atcCode: "C01CA03",
    batchNumber: "NEP-25033-A",
    currentStock: 6,
    unit: "ampoules",
    dailyBurnRate: 5,
    expiryDate: iso(addDays(today, 3)),
    certStatus: "valid",
    certAuthority: "EMA",
    certNumber: "EU/1/17/0442",
    analogues: [
      { id: "an-12", drugName: "Levophed 4mg/4mL", inn: "Norepinephrine bitartrate", unit: "ampoules", rxcui: "242969", matchScore: 99, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 0, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 48, "fac-stluke": 0, "fac-mercy": 0 } },
      { id: "an-13", drugName: "Phenylephrine 10mg/mL", inn: "Phenylephrine hydrochloride", unit: "ampoules", rxcui: "1114874", matchScore: 71, equivalence: "therapeutic", source: "ATC/WHO", stockByFacility: { "fac-central": 90, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 320, "fac-stluke": 60, "fac-mercy": 0 } },
      { id: "an-14", drugName: "Vasopressin 20U/mL", inn: "Vasopressin", unit: "ampoules", rxcui: "1546028", matchScore: 64, equivalence: "same-class", source: "OpenFDA", stockByFacility: { "fac-central": 24, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 110, "fac-stluke": 40, "fac-mercy": 0 } },
    ],
  },
  {
    id: "inv-006",
    facilityId: "fac-central",
    drugName: "Azithromycin 250mg",
    form: "Film-coated tablet",
    inn: "Azithromycin",
    atcCode: "J01FA10",
    batchNumber: "AZT-24610-B",
    currentStock: 520,
    unit: "boxes",
    dailyBurnRate: 24,
    expiryDate: iso(addDays(today, 190)),
    certStatus: "valid",
    certAuthority: "FDA",
    certNumber: "ANDA-078112",
    analogues: [],
  },
  {
    id: "inv-007",
    facilityId: "fac-central",
    drugName: "Insulin Glargine 100U/mL",
    form: "Prefilled pen, 3mL",
    inn: "Insulin glargine",
    atcCode: "A10AE04",
    batchNumber: "IGL-25102-A",
    currentStock: 26,
    unit: "pens",
    dailyBurnRate: 9,
    expiryDate: iso(addDays(today, 27)),
    certStatus: "expired",
    certAuthority: "EMA",
    certNumber: "EU/1/00/134 (lapsed)",
    analogues: [
      { id: "an-15", drugName: "Lantus SoloStar", inn: "Insulin glargine", unit: "pens", rxcui: "1157459", matchScore: 99, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 55, "fac-riverside": 40, "fac-westend": 12, "fac-warehouse-n": 380, "fac-stluke": 70, "fac-mercy": 150 } },
      { id: "an-16", drugName: "Toujeo 300U/mL", inn: "Insulin glargine", unit: "pens", rxcui: "1605101", matchScore: 88, equivalence: "bioequivalent", source: "OpenFDA", stockByFacility: { "fac-central": 0, "fac-riverside": 18, "fac-westend": 0, "fac-warehouse-n": 140, "fac-stluke": 0, "fac-mercy": 60 } },
      { id: "an-17", drugName: "Insulin Detemir 100U/mL", inn: "Insulin detemir", unit: "pens", rxcui: "285018", matchScore: 76, equivalence: "therapeutic", source: "ATC/WHO", stockByFacility: { "fac-central": 30, "fac-riverside": 0, "fac-westend": 8, "fac-warehouse-n": 90, "fac-stluke": 25, "fac-mercy": 0 } },
    ],
  },
  {
    id: "inv-008",
    facilityId: "fac-central",
    drugName: "Midazolam 5mg/mL",
    form: "Injection, 3mL ampoule",
    inn: "Midazolam",
    atcCode: "N05CD08",
    batchNumber: "MDZ-24988-C",
    currentStock: 90,
    unit: "ampoules",
    dailyBurnRate: 7,
    expiryDate: iso(addDays(today, 55)),
    certStatus: "valid",
    certAuthority: "FDA",
    certNumber: "ANDA-071980",
    analogues: [],
  },
  {
    id: "inv-009",
    facilityId: "fac-central",
    drugName: "Paracetamol 1g IV",
    form: "Infusion bag, 100mL",
    inn: "Paracetamol",
    atcCode: "N02BE01",
    batchNumber: "PCM-25064-B",
    currentStock: 410,
    unit: "bags",
    dailyBurnRate: 48,
    expiryDate: iso(addDays(today, 410)),
    certStatus: "valid",
    certAuthority: "FDA",
    certNumber: "NDA-021831",
    analogues: [],
  },
  {
    id: "inv-010",
    facilityId: "fac-central",
    drugName: "Heparin Sodium 5000IU/mL",
    form: "Injection, 5mL vial",
    inn: "Heparin sodium",
    atcCode: "B01AB01",
    batchNumber: "HEP-24855-A",
    currentStock: 5,
    unit: "vials",
    dailyBurnRate: 4,
    expiryDate: iso(addDays(today, 14)),
    certStatus: "pending",
    certAuthority: "EMA",
    certNumber: "EU/1/12/778 (renewal filed)",
    analogues: [
      { id: "an-18", drugName: "Heparin Sodium (generic) 5000IU/mL", inn: "Heparin sodium", unit: "vials", rxcui: "1361574", matchScore: 98, equivalence: "bioequivalent", source: "RxNorm", stockByFacility: { "fac-central": 22, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 260, "fac-stluke": 12, "fac-mercy": 64 } },
      { id: "an-19", drugName: "Enoxaparin 40mg/0.4mL", inn: "Enoxaparin sodium", unit: "syringes", rxcui: "854235", matchScore: 79, equivalence: "therapeutic", source: "ATC/WHO", stockByFacility: { "fac-central": 140, "fac-riverside": 55, "fac-westend": 30, "fac-warehouse-n": 700, "fac-stluke": 95, "fac-mercy": 210 } },
      { id: "an-20", drugName: "Fondaparinux 2.5mg", inn: "Fondaparinux sodium", unit: "syringes", rxcui: "321208", matchScore: 63, equivalence: "same-class", source: "OpenFDA", stockByFacility: { "fac-central": 0, "fac-riverside": 0, "fac-westend": 0, "fac-warehouse-n": 85, "fac-stluke": 20, "fac-mercy": 0 } },
    ],
  },
];

// Each site stocks a different slice of the formulary at different depths:
// clinics don't carry ICU drugs and hold days, not weeks; the warehouse
// holds bulk with a slow burn. Derived from the canonical list above so the
// deliberate "story" rows (pending certs, shortages) survive at Central.
const FACILITY_PROFILE: Record<string, { stockFactor: number; burnFactor: number; skuFactor: number; absent: string[] }> = {
  "fac-central": { stockFactor: 1, burnFactor: 1, skuFactor: 1, absent: [] },
  "fac-riverside": { stockFactor: 0.35, burnFactor: 0.4, skuFactor: 0.38, absent: ["inv-002", "inv-005"] },
  "fac-westend": { stockFactor: 0.22, burnFactor: 0.3, skuFactor: 0.26, absent: ["inv-002", "inv-005", "inv-008"] },
  "fac-warehouse-n": { stockFactor: 7, burnFactor: 0.15, skuFactor: 1.6, absent: ["inv-007"] },
};

export function inventoryFor(facilityId: string): InventoryItem[] {
  const profile = FACILITY_PROFILE[facilityId] ?? FACILITY_PROFILE["fac-central"];
  const suffix = facilityId.slice(-2).toUpperCase();
  return inventory
    .filter((item) => !profile.absent.includes(item.id))
    .map((item) => ({
      ...item,
      facilityId,
      currentStock: Math.max(0, Math.round(item.currentStock * profile.stockFactor)),
      dailyBurnRate: Math.max(1, Math.round(item.dailyBurnRate * profile.burnFactor)),
      batchNumber: facilityId === "fac-central" ? item.batchNumber : `${item.batchNumber}-${suffix}`,
    }));
}

export interface InventoryKpis {
  totalSkus: number;
  criticalStock: number;
  expiringSoon: number;
  pendingCerts: number;
}

// Computed from the facility's real list rather than hardcoded, so the
// tiles actually change when you switch site instead of lying.
export function inventoryKpisFor(facilityId: string): InventoryKpis {
  const items = inventoryFor(facilityId);
  const profile = FACILITY_PROFILE[facilityId] ?? FACILITY_PROFILE["fac-central"];
  return {
    totalSkus: Math.round(1240 * profile.skuFactor),
    criticalStock: items.filter((i) => stockRisk(i) === "critical").length,
    expiringSoon: items.filter((i) => daysUntil(i.expiryDate) <= 30).length,
    pendingCerts: items.filter((i) => i.certStatus !== "valid").length,
  };
}

// ---------------------------------------------------------------------
// Audit Log & Compliance (per-SKU history)
// ---------------------------------------------------------------------

export type AuditActorType = "clinician" | "ai" | "system" | "regulator";

export interface AuditEntry {
  id: string;
  timestamp: string; // ISO, always relative to `today` so it stays current
  actor: string;
  actorType: AuditActorType;
  action: string;
  refId?: string;
}

function auditTs(daysAgo: number, hour: number, minute: number): string {
  const d = addDays(today, -daysAgo);
  d.setUTCHours(hour, minute, 0, 0);
  return d.toISOString();
}

export function formatAuditTimestamp(dateIso: string): string {
  const d = new Date(dateIso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

// Any SKU without a curated log below falls back to this generic-but-real
// looking system trail, so every "Audit Log" click has something to show.
const DEFAULT_AUDIT_LOG: AuditEntry[] = [
  { id: "a-def-1", timestamp: auditTs(0, 7, 40), actor: "Warehouse System", actorType: "system", action: "synced stock count with regional warehouse" },
  { id: "a-def-2", timestamp: auditTs(2, 16, 5), actor: "ML Pipeline", actorType: "ai", action: "updated the remaining stock forecast", refId: "trigger: batch consumption" },
  { id: "a-def-3", timestamp: auditTs(6, 11, 20), actor: "OCR Engine", actorType: "ai", action: "re-verified certificate against manufacturer registry" },
];

export const auditLog: Record<string, AuditEntry[]> = {
  "inv-001": [
    { id: "a-001-1", timestamp: auditTs(0, 14, 22), actor: "Dr. Smirnov", actorType: "clinician", action: "confirmed the switch to Augmentin", refId: "#B-9021" },
    { id: "a-001-2", timestamp: auditTs(1, 9, 15), actor: "ML Pipeline", actorType: "ai", action: "updated the remaining stock forecast", refId: "trigger: batch consumption" },
    { id: "a-001-3", timestamp: auditTs(4, 18, 0), actor: "FDA certificate", actorType: "regulator", action: "verified", refId: "OCR Engine" },
    { id: "a-001-4", timestamp: auditTs(9, 8, 45), actor: "Dr. Smirnov", actorType: "clinician", action: "received batch into inventory", refId: "AMX-24118-B" },
  ],
  "inv-003": [
    { id: "a-003-1", timestamp: auditTs(0, 8, 10), actor: "Compliance Bot", actorType: "regulator", action: "flagged FDA renewal filing as still pending", refId: "ANDA-065432" },
    { id: "a-003-2", timestamp: auditTs(2, 13, 30), actor: "Nurse Okafor", actorType: "clinician", action: "logged administration from active batch", refId: "CFX-25011-A" },
    { id: "a-003-3", timestamp: auditTs(5, 17, 50), actor: "ML Pipeline", actorType: "ai", action: "escalated stockout risk to critical", refId: "days of supply < 3" },
  ],
  "inv-005": [
    { id: "a-005-1", timestamp: auditTs(0, 6, 5), actor: "FDA Shortage Feed", actorType: "regulator", action: "confirmed national backorder continues through Q4" },
    { id: "a-005-2", timestamp: auditTs(1, 12, 40), actor: "Dr. Smirnov", actorType: "clinician", action: "requested inter-facility transfer from Regional Warehouse North", refId: "TR-4821" },
    { id: "a-005-3", timestamp: auditTs(3, 9, 0), actor: "ML Pipeline", actorType: "ai", action: "updated the remaining stock forecast", refId: "trigger: batch consumption" },
  ],
  "inv-007": [
    { id: "a-007-1", timestamp: auditTs(0, 10, 15), actor: "Compliance Bot", actorType: "regulator", action: "flagged certificate as lapsed", refId: "EU/1/00/134" },
    { id: "a-007-2", timestamp: auditTs(3, 15, 25), actor: "OCR Engine", actorType: "ai", action: "re-scanned renewal filing, still awaiting EMA approval" },
    { id: "a-007-3", timestamp: auditTs(11, 8, 0), actor: "Dr. Smirnov", actorType: "clinician", action: "received batch into inventory", refId: "IGL-25102-A" },
  ],
  "inv-010": [
    { id: "a-010-1", timestamp: auditTs(0, 9, 5), actor: "Compliance Bot", actorType: "regulator", action: "flagged EMA renewal filing as still pending", refId: "EU/1/12/778" },
    { id: "a-010-2", timestamp: auditTs(2, 14, 50), actor: "Nurse Okafor", actorType: "clinician", action: "logged administration from active batch", refId: "HEP-24855-A" },
    { id: "a-010-3", timestamp: auditTs(7, 18, 0), actor: "ML Pipeline", actorType: "ai", action: "escalated stockout risk to critical", refId: "days of supply < 3" },
  ],
};

export function auditLogFor(itemId: string): AuditEntry[] {
  return auditLog[itemId] ?? DEFAULT_AUDIT_LOG;
}

// ---------------------------------------------------------------------
// System status / audit trail (header pill + footer)
// ---------------------------------------------------------------------

export const systemStatus = {
  rxNormSyncMinutesAgo: 4,
  gkeCluster: "gke-europe-west3-a",
  auditHash: "7f8a3c1e9d2b6f04",
  complianceStandard: "ISO-13485 Compliant Workflow",
};

// ---------------------------------------------------------------------
// Restock & Forecasts
// ---------------------------------------------------------------------

export interface ForecastPoint {
  date: string;
  actual: number | null;
  forecast: number | null;
  forecastLow: number | null;
  forecastHigh: number | null;
}

// Procurement facts a par-level calculation can't derive on its own —
// quantity and coverage are computed (see reorderPoint/parLevel below),
// not authored here.
export interface ForecastPurchaseOrder {
  supplier: string;
  unit: string;
  unitCost: number;
  leadTimeDays: number;
}

// A trained model's own baseline — not facility-scoped. forecastFor() below
// joins this to a facility's InventoryItem and scales it, the same way
// inventoryFor() scales the canonical `inventory` array.
export interface ForecastModel {
  itemId: string; // matches InventoryItem.id — same SKU, not a parallel one
  model: string;
  seasonalityFactor: string;
  confidence: number;
  series: ForecastPoint[];
  purchaseOrder: ForecastPurchaseOrder;
}

// What the forecasts page actually renders: a facility-scoped item joined
// to its model. `item.currentStock` / `item.dailyBurnRate` already reflect
// the active facility (via inventoryFor); `series` and `purchaseOrder.quantity`
// are scaled the same way here so a clinic doesn't get a warehouse-sized
// suggestion.
export interface ScaledForecast {
  item: InventoryItem;
  model: string;
  seasonalityFactor: string;
  confidence: number;
  series: ForecastPoint[];
  purchaseOrder: ForecastPurchaseOrder;
}

// Deterministic pseudo-history: a base burn rate plus a slow sine-wave
// season and a bit of day-to-day jitter, no randomness library needed.
function buildSeries(base: number, seasonAmplitude: number, seasonPeriodDays: number, surge: number) {
  const series: ForecastPoint[] = [];
  for (let i = -60; i <= 30; i++) {
    const date = iso(addDays(today, i));
    const season = seasonAmplitude * Math.sin((2 * Math.PI * i) / seasonPeriodDays);
    const jitter = 4 * Math.sin(i * 1.7) + 2 * Math.cos(i * 0.9);
    if (i <= 0) {
      const actual = Math.max(0, Math.round(base + season + jitter));
      series.push({ date, actual, forecast: null, forecastLow: null, forecastHigh: null });
    } else {
      // Winter-surge ramp for the forecast window.
      const rampedBase = base + surge * (i / 30);
      const forecast = Math.round(rampedBase + season);
      const band = Math.round(3 + i * 0.6);
      series.push({
        date,
        actual: null,
        forecast,
        forecastLow: Math.max(0, forecast - band),
        forecastHigh: forecast + band,
      });
    }
  }
  return series;
}

// Keyed by the same ids as `inventory` (inv-001 Amoxicillin/Clavulanate,
// inv-002 Propofol, inv-006 Azithromycin) — these used to be a parallel
// `fc-*` id space with their own drug names and stock figures that quietly
// disagreed with Inventory for the same SKU. Only 3 of the 10 catalogue
// SKUs have a trained model; forecastFor() returns null for the rest and
// the page shows an explicit empty state rather than pretending otherwise.
export const forecastModels: ForecastModel[] = [
  {
    itemId: "inv-001",
    model: "Prophet v1.2",
    seasonalityFactor: "+35% Winter Surge",
    confidence: 94.2,
    series: buildSeries(58, 6, 14, 22),
    purchaseOrder: { supplier: "PharmaSource Global Ltd.", unit: "boxes", unitCost: 12.4, leadTimeDays: 5 },
  },
  {
    itemId: "inv-002",
    model: "Prophet v1.2",
    seasonalityFactor: "+12% Elective Surgery Backlog",
    confidence: 89.7,
    series: buildSeries(17, 2, 10, 6),
    purchaseOrder: { supplier: "Meditech Distribution Co.", unit: "ampoules", unitCost: 3.85, leadTimeDays: 7 },
  },
  {
    itemId: "inv-006",
    model: "XGBoost v0.9",
    seasonalityFactor: "+28% Respiratory Season",
    confidence: 91.5,
    series: buildSeries(23, 5, 12, 14),
    purchaseOrder: { supplier: "PharmaSource Global Ltd.", unit: "boxes", unitCost: 6.1, leadTimeDays: 4 },
  },
];

export function forecastableItemIds(): string[] {
  return forecastModels.map((m) => m.itemId);
}

// --- Reorder point & par level ------------------------------------------
// The number every "how much should I order/hold" question in the app used
// to answer with a stored literal. A reorder point is the stock level that
// should trigger replenishment — enough to cover the lead time plus a
// safety margin. A par level is what replenishment restores it to.
const SAFETY_BUFFER_DAYS = 3;
const PAR_TARGET_DAYS = 30;
// Flat fallback for the 7 catalogue SKUs with no assigned default supplier
// — median of the 4 suppliers' lead times (3, 5, 7, 11). The 3 forecastable
// SKUs use their model's own curated supplier/lead time instead.
const DEFAULT_LEAD_TIME_DAYS = 6;

export function leadTimeDaysFor(itemId: string): number {
  return forecastModels.find((m) => m.itemId === itemId)?.purchaseOrder.leadTimeDays ?? DEFAULT_LEAD_TIME_DAYS;
}

export function reorderPoint(item: Pick<InventoryItem, "id" | "dailyBurnRate">): number {
  return Math.ceil(item.dailyBurnRate * (leadTimeDaysFor(item.id) + SAFETY_BUFFER_DAYS));
}

// dailyRate is a parameter rather than always reading InventoryItem.dailyBurnRate
// so the Forecasts page can target the model's own predicted rate instead
// of the item's historical burn rate — "par level per the AI forecast" is a
// different, and for that card more honest, number than "par level per
// last month's average."
export function parLevel(dailyRate: number, targetCoverageDays: number = PAR_TARGET_DAYS): number {
  return Math.ceil(dailyRate * targetCoverageDays);
}

// Joins a trained model to the facility's actual stock and scales the
// series by the same burnFactor inventoryFor() uses for dailyBurnRate, so a
// clinic sees clinic-sized numbers, not a warehouse's. Returns null when
// the SKU has no trained model, or isn't stocked at this facility at all.
export function forecastFor(facilityId: string, itemId: string): ScaledForecast | null {
  const item = inventoryFor(facilityId).find((i) => i.id === itemId);
  const meta = forecastModels.find((m) => m.itemId === itemId);
  if (!item || !meta) return null;

  const profile = FACILITY_PROFILE[facilityId] ?? FACILITY_PROFILE["fac-central"];
  const scale = (n: number | null) => (n == null ? null : Math.round(n * profile.burnFactor));
  const series = meta.series.map((p) => ({
    date: p.date,
    actual: scale(p.actual),
    forecast: scale(p.forecast),
    forecastLow: p.forecastLow == null ? null : Math.max(0, scale(p.forecastLow)!),
    forecastHigh: scale(p.forecastHigh),
  }));

  return { item, model: meta.model, seasonalityFactor: meta.seasonalityFactor, confidence: meta.confidence, series, purchaseOrder: meta.purchaseOrder };
}

// ---------------------------------------------------------------------
// Shortage & Regional Matrix
// ---------------------------------------------------------------------

export interface ShortageAlert {
  id: string;
  drugName: string;
  inn: string;
  itemId: string; // resolves the alert to the same SKU inventoryFor() knows about
  source: "FDA" | "EMA";
  severity: "critical" | "warning";
  note: string;
}

export const shortageAlerts: ShortageAlert[] = [
  { id: "sa-01", drugName: "Norepinephrine 4mg/4mL", inn: "Norepinephrine bitartrate", itemId: "inv-005", source: "FDA", severity: "critical", note: "Manufacturing delay, national backorder through Q4." },
  { id: "sa-02", drugName: "Ceftriaxone 1g", inn: "Ceftriaxone sodium", itemId: "inv-003", source: "EMA", severity: "warning", note: "Reduced allocation, 2 of 3 suppliers affected." },
  { id: "sa-03", drugName: "Heparin Sodium 5000IU/mL", inn: "Heparin sodium", itemId: "inv-010", source: "FDA", severity: "warning", note: "Raw material shortage reported by manufacturer." },
];

export interface FacilityStockRow {
  id: string;
  facilityId: string;
  units: number;
  daysOfSupply: number;
  // false for partner sites (St. Luke, Mercy) — we don't operate them, so
  // this is a hand-maintained estimate rather than something inventoryFor()
  // actually computed. true for every operated facility.
  measured: boolean;
}

// Partner-facility figures only — sites we don't operate and can't derive
// from inventoryFor(). Missing entries mean we have no visibility into that
// site for that alert, not that they hold zero stock.
const PARTNER_SHORTAGE_STOCK: Record<string, Record<string, { units: number; daysOfSupply: number }>> = {
  "sa-01": {
    "fac-stluke": { units: 0, daysOfSupply: 0 },
    "fac-mercy": { units: 0, daysOfSupply: 0 },
  },
  "sa-02": {
    "fac-stluke": { units: 210, daysOfSupply: 70 },
  },
  "sa-03": {
    "fac-stluke": { units: 12, daysOfSupply: 8 },
    "fac-mercy": { units: 64, daysOfSupply: 61 },
  },
};

// Derived from inventoryFor() for every operated facility, so this can
// never disagree with what Inventory shows for the same SKU — the matrix
// used to be a hand-maintained table that drifted from FACILITY_PROFILE's
// `absent` list (e.g. it showed Norepinephrine in stock at facilities whose
// profile excludes it). Partner facilities (not operated) fall back to a
// hand-authored estimate, or are omitted if we have none for that alert.
export function shortageRowsFor(alertId: string): FacilityStockRow[] {
  const alert = shortageAlerts.find((a) => a.id === alertId);
  if (!alert) return [];

  const rows: FacilityStockRow[] = [];
  for (const f of facilities) {
    if (f.operated) {
      const item = inventoryFor(f.id).find((i) => i.id === alert.itemId);
      rows.push({
        id: `f-${f.id}`,
        facilityId: f.id,
        units: item?.currentStock ?? 0,
        daysOfSupply: item ? daysOfSupply(item) : 0,
        measured: true,
      });
    } else {
      const est = PARTNER_SHORTAGE_STOCK[alertId]?.[f.id];
      if (est) rows.push({ id: `f-${f.id}`, facilityId: f.id, ...est, measured: false });
    }
  }
  return rows;
}

// ---------------------------------------------------------------------
// Purchase & Orders
// ---------------------------------------------------------------------

export interface Supplier {
  id: string;
  name: string;
  shortName: string;
  leadTimeDays: number;
  reliabilityPct: number;
  shippingFlat: number;
  catalog: Record<string, number>; // inventory item id -> unit cost
}

// Unit costs differ per supplier on purpose: switching supplier on the
// order form has to visibly move the estimated total.
export const suppliers: Supplier[] = [
  {
    id: "sup-pharmasource",
    name: "PharmaSource Global Ltd.",
    shortName: "PharmaSource",
    leadTimeDays: 5,
    reliabilityPct: 98.2,
    shippingFlat: 120,
    catalog: { "inv-001": 12.4, "inv-002": 3.85, "inv-003": 8.9, "inv-004": 14.2, "inv-005": 22.5, "inv-006": 6.1, "inv-007": 41.0, "inv-008": 5.4, "inv-009": 3.2, "inv-010": 9.75 },
  },
  {
    id: "sup-meditech",
    name: "Meditech Distribution Co.",
    shortName: "Meditech",
    leadTimeDays: 7,
    reliabilityPct: 95.6,
    shippingFlat: 80,
    catalog: { "inv-001": 11.8, "inv-002": 4.05, "inv-003": 8.2, "inv-004": 15.1, "inv-005": 21.4, "inv-006": 6.55, "inv-007": 43.5, "inv-008": 5.1, "inv-009": 3.45, "inv-010": 9.1 },
  },
  {
    id: "sup-europharm",
    name: "EuroPharm Wholesale AG",
    shortName: "EuroPharm",
    leadTimeDays: 3,
    reliabilityPct: 99.1,
    shippingFlat: 210,
    catalog: { "inv-001": 13.6, "inv-002": 4.2, "inv-003": 9.6, "inv-004": 13.4, "inv-005": 24.8, "inv-006": 6.9, "inv-007": 39.2, "inv-008": 6.0, "inv-009": 3.6, "inv-010": 10.4 },
  },
  {
    id: "sup-nordic",
    name: "Nordic Medical Supply",
    shortName: "Nordic",
    leadTimeDays: 11,
    reliabilityPct: 92.4,
    shippingFlat: 45,
    catalog: { "inv-001": 10.9, "inv-002": 3.6, "inv-003": 7.8, "inv-004": 16.0, "inv-005": 20.1, "inv-006": 5.7, "inv-007": 45.8, "inv-008": 4.85, "inv-009": 2.95, "inv-010": 8.6 },
  },
];

export function supplierById(id: string): Supplier {
  return suppliers.find((s) => s.id === id) ?? suppliers[0];
}

export type OrderStatus = "draft" | "placed" | "in_transit" | "delivered" | "cancelled";
export type OrderSource = "ai_suggestion" | "manual";

export interface PurchaseOrder {
  id: string;
  facilityId: string;
  supplierId: string;
  drugId: string;
  drugName: string;
  quantity: number;
  unit: string;
  unitCost: number;
  shipping: number;
  status: OrderStatus;
  source: OrderSource;
  createdAt: string;
  expectedDelivery: string;
  note?: string;
}

export function orderTotal(order: Pick<PurchaseOrder, "quantity" | "unitCost" | "shipping">): number {
  return order.quantity * order.unitCost + order.shipping;
}

// Seeded history so the Purchase & Orders page has something to show on
// first load; new orders from the forecast page and the order form are
// prepended to this in OrdersProvider.
export const seedOrders: PurchaseOrder[] = [
  { id: "PO-2026-0148", facilityId: "fac-central", supplierId: "sup-europharm", drugId: "inv-005", drugName: "Norepinephrine 4mg/4mL", quantity: 120, unit: "ampoules", unitCost: 24.8, shipping: 210, status: "in_transit", source: "ai_suggestion", createdAt: iso(addDays(today, -2)), expectedDelivery: iso(addDays(today, 1)), note: "Expedited against FDA national backorder." },
  { id: "PO-2026-0147", facilityId: "fac-central", supplierId: "sup-pharmasource", drugId: "inv-003", drugName: "Ceftriaxone 1g", quantity: 300, unit: "vials", unitCost: 8.9, shipping: 120, status: "in_transit", source: "manual", createdAt: iso(addDays(today, -3)), expectedDelivery: iso(addDays(today, 2)) },
  { id: "PO-2026-0146", facilityId: "fac-riverside", supplierId: "sup-meditech", drugId: "inv-001", drugName: "Amoxicillin/Clavulanate 875mg", quantity: 220, unit: "boxes", unitCost: 11.8, shipping: 80, status: "placed", source: "manual", createdAt: iso(addDays(today, -4)), expectedDelivery: iso(addDays(today, 3)) },
  { id: "PO-2026-0145", facilityId: "fac-central", supplierId: "sup-pharmasource", drugId: "inv-010", drugName: "Heparin Sodium 5000IU/mL", quantity: 180, unit: "vials", unitCost: 9.75, shipping: 120, status: "delivered", source: "ai_suggestion", createdAt: iso(addDays(today, -12)), expectedDelivery: iso(addDays(today, -6)), note: "Generated from 91.5% confidence forecast." },
  { id: "PO-2026-0144", facilityId: "fac-warehouse-n", supplierId: "sup-nordic", drugId: "inv-006", drugName: "Azithromycin 250mg", quantity: 1400, unit: "boxes", unitCost: 5.7, shipping: 45, status: "delivered", source: "manual", createdAt: iso(addDays(today, -16)), expectedDelivery: iso(addDays(today, -4)) },
  { id: "PO-2026-0143", facilityId: "fac-westend", supplierId: "sup-meditech", drugId: "inv-004", drugName: "Salbutamol 100mcg Inhaler", quantity: 90, unit: "inhalers", unitCost: 15.1, shipping: 80, status: "delivered", source: "manual", createdAt: iso(addDays(today, -21)), expectedDelivery: iso(addDays(today, -13)) },
  { id: "PO-2026-0142", facilityId: "fac-central", supplierId: "sup-europharm", drugId: "inv-007", drugName: "Insulin Glargine 100U/mL", quantity: 60, unit: "pens", unitCost: 39.2, shipping: 210, status: "cancelled", source: "manual", createdAt: iso(addDays(today, -24)), expectedDelivery: iso(addDays(today, -20)), note: "Cancelled — EMA certificate lapsed before dispatch." },
  { id: "PO-2026-0141", facilityId: "fac-riverside", supplierId: "sup-pharmasource", drugId: "inv-009", drugName: "Paracetamol 1g IV", quantity: 500, unit: "bags", unitCost: 3.2, shipping: 120, status: "delivered", source: "ai_suggestion", createdAt: iso(addDays(today, -29)), expectedDelivery: iso(addDays(today, -23)) },
];
