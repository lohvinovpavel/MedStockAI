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

// ---------------------------------------------------------------------
// Inventory & Batches
// ---------------------------------------------------------------------

export type StockRisk = "critical" | "warning" | "normal";
export type CertStatus = "valid" | "pending" | "expired";

export interface AnalogueOption {
  id: string;
  drugName: string;
  inn: string;
  facility: string;
  distanceKm: number;
  stock: number;
  unit: string;
}

export interface InventoryItem {
  id: string;
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

export function stockRisk(item: Pick<InventoryItem, "currentStock" | "dailyBurnRate">): StockRisk {
  const days = daysOfSupply(item);
  if (days <= 3) return "critical";
  if (days <= 10) return "warning";
  return "normal";
}

export { daysOfSupply };

export const inventory: InventoryItem[] = [
  {
    id: "inv-001",
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
      { id: "an-01", drugName: "Co-Amoxiclav 875/125mg", inn: "Amoxicillin, Clavulanic acid", facility: "Sub-store B2", distanceKm: 0, stock: 340, unit: "boxes" },
      { id: "an-02", drugName: "Augmentin 875mg", inn: "Amoxicillin, Clavulanic acid", facility: "Sub-store C1", distanceKm: 0, stock: 96, unit: "boxes" },
    ],
  },
  {
    id: "inv-002",
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
      { id: "an-03", drugName: "Diprivan 1%", inn: "Propofol", facility: "Sub-store A1", distanceKm: 0, stock: 60, unit: "ampoules" },
    ],
  },
  {
    id: "inv-003",
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
      { id: "an-04", drugName: "Rocephin 1g", inn: "Ceftriaxone sodium", facility: "Sub-store B2", distanceKm: 0, stock: 4, unit: "vials" },
      { id: "an-05", drugName: "Ceftriaxone 1g (generic)", inn: "Ceftriaxone sodium", facility: "St. Luke Hospital", distanceKm: 12, stock: 210, unit: "vials" },
    ],
  },
  {
    id: "inv-004",
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
      { id: "an-06", drugName: "Levophed 4mg/4mL", inn: "Norepinephrine bitartrate", facility: "Regional Warehouse North", distanceKm: 34, stock: 48, unit: "ampoules" },
    ],
  },
  {
    id: "inv-006",
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
      { id: "an-07", drugName: "Lantus SoloStar", inn: "Insulin glargine", facility: "Sub-store A1", distanceKm: 0, stock: 55, unit: "pens" },
    ],
  },
  {
    id: "inv-008",
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
      { id: "an-08", drugName: "Heparin Sodium (generic) 5000IU/mL", inn: "Heparin sodium", facility: "Sub-store C1", distanceKm: 0, stock: 22, unit: "vials" },
    ],
  },
];

export const inventoryKpis = {
  totalSkus: 1240,
  criticalStock: 8,
  expiringSoon: 14,
  pendingCerts: 3,
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

export interface ForecastDrug {
  id: string;
  name: string;
  unit: string;
  model: string;
  seasonalityFactor: string;
  confidence: number;
  series: ForecastPoint[];
  purchaseOrder: {
    supplier: string;
    quantity: number;
    unit: string;
    coverageDays: number;
    unitCost: number;
    leadTimeDays: number;
  };
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

export const forecastDrugs: ForecastDrug[] = [
  {
    id: "fc-amoxicillin",
    name: "Amoxicillin 500mg",
    unit: "boxes/day",
    model: "Prophet v1.2",
    seasonalityFactor: "+35% Winter Surge",
    confidence: 94.2,
    series: buildSeries(58, 6, 14, 22),
    purchaseOrder: {
      supplier: "PharmaSource Global Ltd.",
      quantity: 150,
      unit: "boxes",
      coverageDays: 30,
      unitCost: 12.4,
      leadTimeDays: 5,
    },
  },
  {
    id: "fc-propofol",
    name: "Propofol 1%",
    unit: "ampoules/day",
    model: "Prophet v1.2",
    seasonalityFactor: "+12% Elective Surgery Backlog",
    confidence: 89.7,
    series: buildSeries(17, 2, 10, 6),
    purchaseOrder: {
      supplier: "Meditech Distribution Co.",
      quantity: 480,
      unit: "ampoules",
      coverageDays: 30,
      unitCost: 3.85,
      leadTimeDays: 7,
    },
  },
  {
    id: "fc-azithromycin",
    name: "Azithromycin 250mg",
    unit: "boxes/day",
    model: "XGBoost v0.9",
    seasonalityFactor: "+28% Respiratory Season",
    confidence: 91.5,
    series: buildSeries(23, 5, 12, 14),
    purchaseOrder: {
      supplier: "PharmaSource Global Ltd.",
      quantity: 720,
      unit: "boxes",
      coverageDays: 30,
      unitCost: 6.1,
      leadTimeDays: 4,
    },
  },
];

// ---------------------------------------------------------------------
// Shortage & Regional Matrix
// ---------------------------------------------------------------------

export interface ShortageAlert {
  id: string;
  drugName: string;
  inn: string;
  source: "FDA" | "EMA";
  severity: "critical" | "warning";
  note: string;
}

export const shortageAlerts: ShortageAlert[] = [
  { id: "sa-01", drugName: "Norepinephrine 4mg/4mL", inn: "Norepinephrine bitartrate", source: "FDA", severity: "critical", note: "Manufacturing delay, national backorder through Q4." },
  { id: "sa-02", drugName: "Ceftriaxone 1g", inn: "Ceftriaxone sodium", source: "EMA", severity: "warning", note: "Reduced allocation, 2 of 3 suppliers affected." },
  { id: "sa-03", drugName: "Heparin Sodium 5000IU/mL", inn: "Heparin sodium", source: "FDA", severity: "warning", note: "Raw material shortage reported by manufacturer." },
];

export interface FacilityStockRow {
  id: string;
  facility: string;
  type: "Hospital" | "Clinic" | "Pharmacy";
  distanceKm: number;
  units: number;
  daysOfSupply: number;
}

// Matrix is keyed by drug id so switching the focus drug swaps the whole
// facility list — same shape as forecastDrugs on purpose.
export const shortageMatrix: Record<string, FacilityStockRow[]> = {
  "sa-01": [
    { id: "f-01", facility: "Central Hospital (this facility)", type: "Hospital", distanceKm: 0, units: 6, daysOfSupply: 1 },
    { id: "f-02", facility: "St. Luke Hospital", type: "Hospital", distanceKm: 12, units: 0, daysOfSupply: 0 },
    { id: "f-03", facility: "Regional Warehouse North", type: "Hospital", distanceKm: 34, units: 48, daysOfSupply: 68 },
    { id: "f-04", facility: "Riverside Clinic #4", type: "Clinic", distanceKm: 19, units: 2, daysOfSupply: 2 },
    { id: "f-05", facility: "Mercy Pharmacy Network", type: "Pharmacy", distanceKm: 27, units: 0, daysOfSupply: 0 },
    { id: "f-06", facility: "West End Community Clinic", type: "Clinic", distanceKm: 41, units: 30, daysOfSupply: 45 },
  ],
  "sa-02": [
    { id: "f-01", facility: "Central Hospital (this facility)", type: "Hospital", distanceKm: 0, units: 9, daysOfSupply: 1 },
    { id: "f-02", facility: "St. Luke Hospital", type: "Hospital", distanceKm: 12, units: 210, daysOfSupply: 70 },
    { id: "f-03", facility: "Regional Warehouse North", type: "Hospital", distanceKm: 34, units: 340, daysOfSupply: 95 },
    { id: "f-04", facility: "Riverside Clinic #4", type: "Clinic", distanceKm: 19, units: 0, daysOfSupply: 0 },
  ],
  "sa-03": [
    { id: "f-01", facility: "Central Hospital (this facility)", type: "Hospital", distanceKm: 0, units: 5, daysOfSupply: 1 },
    { id: "f-02", facility: "St. Luke Hospital", type: "Hospital", distanceKm: 12, units: 12, daysOfSupply: 8 },
    { id: "f-05", facility: "Mercy Pharmacy Network", type: "Pharmacy", distanceKm: 27, units: 64, daysOfSupply: 61 },
  ],
};
