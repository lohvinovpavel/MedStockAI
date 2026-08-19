"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { Search, TriangleAlert } from "lucide-react";
import { Callout } from "@/components/dashboard/Callout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PatientPicker } from "@/components/dashboard/PatientPicker";
import { ImpactWindow } from "@/components/ImpactWindow";
import { AnatomyImpact, type OrganImpact } from "@/components/AnatomyImpact";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StockBand } from "@/components/StockBand";
import { apiFetch } from "@/lib/api";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { AnatomyImpact, type OrganImpact } from "@/components/AnatomyImpact";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

type DrugHit = {
  rxcui: string;
  name: string;
  tty?: string;
  strength?: string;
  dose_form?: string;
  in_formulary?: boolean;
};

type CartItem = {
  id: string;
  rxcui: string;
  name: string;
};

type Patient = {
  id: string;
  full_name: string;
  date_of_birth: string;
  blood_group: string | null;
  allergy_codes: string[];
  condition_codes: string[];
};

type Warning = {
  code: string;
  severity: string;
  message: string;
  source: string;
};

type CartLineResult = {
  rxcui: string;
  name: string | null;
  verdict: string;
  // Where this line lands on the body. Computed server-side from the findings
  // that fired, so the figure and the audit trail cannot disagree.
  organs?: OrganImpact[];
  organs_unmapped?: string[];
  warnings: Warning[];
  exclude_ingredient: string | null;
  exclude_ingredient_name: string | null;
  score?: number | null;
};

type AnalogueHit = {
  rxcui: string;
  name: string;
  quantity?: number;
  stock_status?: string;
  reason?: string;
  citation?: string;
  availability?: {
    quantity: number;
    unit: string;
    nearest_with_stock: { name: string; quantity: number; distance_km: number } | null;
  } | null;
};

/** One candidate assessed against the patient who would receive it. */
type AnalogueVerdict = {
  rxcui: string;
  verdict: string;
  score?: number | null;
  findings: Warning[];
  /** What this drug adds on top of the patient's existing regimen, as opposed
   *  to how it scores alone. Lower is better. */
  added_burden?: number;
  /** Organs it stacks onto that the rest of the regimen already loads. */
  compounds?: string[];
  // Where on the body this candidate bears, derived server-side from the
  // findings above. Optional because an older patient-profiling will not send
  // it, and a missing diagram is better than a guessed one.
  organs?: OrganImpact[];
  organs_unmapped?: string[];
};

// Higher is safer. Mirrors the verdicts patient-profiling returns; a blocked
// candidate carries no score at all, so ordering has to come from the verdict
// rather than the number.
const VERDICT_SAFETY: Record<string, number> = {
  blocked: 0,
  red: 1,
  amber: 2,
  green: 3,
};

// A candidate nobody assessed is not a candidate that passed. It sorts below
// green so an assessed-safe option always outranks an unknown one, and above
// amber so an unknown is not treated as a finding against it.
const UNASSESSED_SAFETY = 2.5;

/**
 * Safest first, then most stock.
 *
 * The analogue service ranks purely by hospital quantity, because it never sees
 * the patient. Once each candidate has been assessed, keeping that order would
 * put the drug there is most of above the one that is right for this person —
 * directly under the physician's cursor. Blocked candidates stay in the list
 * and sort last: dropping them would hide that an obvious substitute was
 * considered and ruled out, which is a thing worth knowing.
 */
/** How many substitutes to offer.
 *
 *  A physician mid-prescription is choosing, not browsing. Twenty ranked
 *  options is a list to read; five is a decision to make. The cut is on the
 *  ranked list, so what is dropped is always what ranked worst. */
const MAX_SUGGESTIONS = 5;

function bySafetyThenStock(
  verdicts: Map<string, AnalogueVerdict>,
): (a: AnalogueHit, b: AnalogueHit) => number {
  const safety = (h: AnalogueHit) => {
    const verdict = verdicts.get(h.rxcui)?.verdict;
    return verdict === undefined
      ? UNASSESSED_SAFETY
      : (VERDICT_SAFETY[verdict] ?? UNASSESSED_SAFETY);
  };
  // What the drug adds to THIS patient, on top of what they already take.
  // Lower is better; undefined sorts last so an unassessed candidate never
  // outranks one we actually checked.
  const burden = (h: AnalogueHit) =>
    verdicts.get(h.rxcui)?.added_burden ?? Number.MAX_SAFE_INTEGER;
  // In stock beats out of stock at equal safety — a safer drug the hospital
  // cannot dispense today is not the better answer to "what do I prescribe
  // now", but it must never outrank a genuinely safer one, so this is the
  // last tiebreak rather than the first.
  const stocked = (h: AnalogueHit) => ((h.quantity ?? 0) > 0 ? 1 : 0);
  return (a, b) =>
    safety(b) - safety(a) ||
    burden(a) - burden(b) ||
    stocked(b) - stocked(a) ||
    (b.quantity ?? 0) - (a.quantity ?? 0);
}

type CartState = {
  patientId: string | null;
  items: CartItem[];
};

type PrescriptionSnapshot = {
  patient: Patient;
  items: CartItem[];
  results: CartLineResult[];
  at: string;
};

const STORAGE_KEY = "medstock-prescribe-cart";

function newItemId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function loadCart(): CartState {
  if (typeof window === "undefined") return { patientId: null, items: [] };
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return { patientId: null, items: [] };
    const parsed = JSON.parse(raw) as CartState;
    return {
      patientId: parsed.patientId ?? null,
      items: Array.isArray(parsed.items) ? parsed.items : [],
    };
  } catch {
    return { patientId: null, items: [] };
  }
}

function saveCart(state: CartState) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function codesToInput(codes: string[]) {
  return codes.join(", ");
}

function inputToCodes(raw: string) {
  return raw
    .split(/[,;\n]/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function PrescriptionCart() {
  const { user } = useSession();
  const { facility } = useFacility();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<DrugHit[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [cart, setCart] = useState<CartState>({ patientId: null, items: [] });
  const [hydrated, setHydrated] = useState(false);

  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null);
  const [patientsError, setPatientsError] = useState<string | null>(null);
  const [patientListKey, setPatientListKey] = useState(0);
  const [showPatientForm, setShowPatientForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formDob, setFormDob] = useState("");
  const [formBlood, setFormBlood] = useState("unknown");
  const [formAllergies, setFormAllergies] = useState("");
  const [formConditions, setFormConditions] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [checkResults, setCheckResults] = useState<CartLineResult[]>([]);
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const [openWarningFor, setOpenWarningFor] = useState<string | null>(null);
  const [analogues, setAnalogues] = useState<AnalogueHit[]>([]);
  const [analogueBusy, setAnalogueBusy] = useState(false);
  const [analogueError, setAnalogueError] = useState<string | null>(null);
  const [analogueUsedAi, setAnalogueUsedAi] = useState(false);
  const [analogueRationaleUnavailable, setAnalogueRationaleUnavailable] =
    useState(false);
  // The figure the analogue view draws. Null until an assessment returns it,
  // and the component says so rather than assuming a sex.
  const [patientSex, setPatientSex] = useState<string | null>(null);
  // The whole regimen on one body, as opposed to each line separately. Comes
  // from /cart-check rather than being summed here: the union is a clinical
  // claim, and a front-end that derived it could drift from what was logged.
  const [regimenOrgans, setRegimenOrgans] = useState<OrganImpact[]>([]);
  const [regimenUnmapped, setRegimenUnmapped] = useState<string[]>([]);
  const [analogueVerdicts, setAnalogueVerdicts] = useState<Map<string, AnalogueVerdict>>(
    new Map(),
  );
  // Distinct from an empty verdict map: "not assessed" and "assessed, nothing
  // found" must not render the same, or a failed check looks like a clean bill.
  const [analogueCheckFailed, setAnalogueCheckFailed] = useState(false);

  const [prescription, setPrescription] = useState<PrescriptionSnapshot | null>(null);

  useEffect(() => {
    setCart(loadCart());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    saveCart(cart);
  }, [cart, hydrated]);

  const { setFocus } = useCopilot();

  const resultsByRxcui = useMemo(() => {
    const map = new Map<string, CartLineResult>();
    for (const row of checkResults) map.set(row.rxcui, row);
    return map;
  }, [checkResults]);

  useEffect(() => {
    if (!selectedPatient) {
      setFocus(null);
      return;
    }
    const drug = cart.items[0];
    const allergies = selectedPatient.allergy_codes?.length
      ? `Allergies: ${selectedPatient.allergy_codes.join(", ")}`
      : "No recorded allergies";
    const conditions = selectedPatient.condition_codes?.length
      ? `Conditions: ${selectedPatient.condition_codes.join(", ")}`
      : "";
    const detail = [
      `DOB: ${selectedPatient.date_of_birth}`,
      `Blood: ${selectedPatient.blood_group || "unknown"}`,
      allergies,
      conditions,
      drug ? `Prescribing: ${drug.name} (RxCUI: ${drug.rxcui})` : "",
    ]
      .filter(Boolean)
      .join(" · ");

    setFocus({
      kind: "patient",
      patientId: selectedPatient.id,
      label: `Patient ${selectedPatient.full_name}`,
      detail,
      rxcui: drug?.rxcui,
      drugName: drug?.name,
    });
    return () => {
      setFocus(null);
    };
  }, [selectedPatient, cart.items, setFocus]);

  // The cart is restored from localStorage as a bare patient id, so on mount
  // there is a selection with no record behind it. Fetch that one patient
  // rather than the population it belongs to — the picker searches on demand,
  // and pulling every row to find one was what made a thousand patients
  // unusable.
  useEffect(() => {
    if (!user || !cart.patientId) return;
    if (selectedPatient?.id === cart.patientId) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch("patients", `/patients/${cart.patientId}`);
        if (!cancelled) setSelectedPatient(data);
      } catch (err) {
        if (cancelled) return;
        // A stored id can outlive the row — a rebuilt demo environment reseeds
        // with new uuids. Drop the selection instead of leaving the cart
        // pointing at a patient that no longer exists.
        setCart((prev) => ({ ...prev, patientId: null }));
        setPatientsError(err instanceof Error ? err.message : "failed to load patient");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, cart.patientId, selectedPatient?.id]);

  useEffect(() => {
    if (!cart.patientId || cart.items.length === 0) {
      setCheckResults([]);
      setCheckError(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setCheckBusy(true);
      setCheckError(null);
      try {
        const data = await apiFetch("patients", "/cart-check", {
          method: "POST",
          body: JSON.stringify({
            patient_id: cart.patientId,
            items: cart.items.map((i) => ({ rxcui: i.rxcui, name: i.name })),
          }),
        });
        if (!cancelled) {
          setCheckResults(data.results ?? []);
          setRegimenOrgans(data.regimen_organs ?? []);
          setRegimenUnmapped(data.regimen_organs_unmapped ?? []);
          if (data.sex) setPatientSex(String(data.sex));
        }
      } catch (err) {
        if (!cancelled) {
          setCheckResults([]);
          setCheckError(err instanceof Error ? err.message : "cart check failed");
        }
      } finally {
        if (!cancelled) setCheckBusy(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [cart.patientId, cart.items]);

  async function onSearch(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearchBusy(true);
    setSearchError(null);
    try {
      const data = await apiFetch(
        "analogue",
        `/drugs/search?q=${encodeURIComponent(q)}`,
      );
      setHits(data.items ?? []);
    } catch (err) {
      setHits([]);
      setSearchError(err instanceof Error ? err.message : "search failed");
    } finally {
      setSearchBusy(false);
    }
  }

  function addToCart(hit: DrugHit) {
    setCart((prev) => ({
      ...prev,
      items: [
        ...prev.items,
        { id: newItemId(), rxcui: hit.rxcui, name: hit.name },
      ],
    }));
  }

  function removeFromCart(id: string) {
    setCart((prev) => ({
      ...prev,
      items: prev.items.filter((i) => i.id !== id),
    }));
    setOpenWarningFor((cur) => (cur === id ? null : cur));
  }

  function clearCart() {
    setCart({ patientId: null, items: [] });
    setCheckResults([]);
    setOpenWarningFor(null);
    setAnalogues([]);
    setPrescription(null);
  }

  function startCreatePatient() {
    setEditingId(null);
    setFormName("");
    setFormDob("");
    setFormBlood("unknown");
    setFormAllergies("");
    setFormConditions("avoid_caffeine");
    setFormError(null);
    setShowPatientForm(true);
  }

  function startEditPatient(p: Patient) {
    setEditingId(p.id);
    setFormName(p.full_name);
    setFormDob(p.date_of_birth);
    setFormBlood(p.blood_group ?? "unknown");
    setFormAllergies(codesToInput(p.allergy_codes));
    setFormConditions(codesToInput(p.condition_codes));
    setFormError(null);
    setShowPatientForm(true);
  }

  async function savePatient(e: FormEvent) {
    e.preventDefault();
    setFormBusy(true);
    setFormError(null);
    const payload = {
      full_name: formName.trim(),
      date_of_birth: formDob,
      blood_group: formBlood || null,
      allergy_codes: inputToCodes(formAllergies),
      condition_codes: inputToCodes(formConditions),
    };
    try {
      const saved = editingId
        ? await apiFetch("patients", `/patients/${editingId}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          })
        : await apiFetch("patients", "/patients", {
            method: "POST",
            body: JSON.stringify(payload),
          });
      setSelectedPatient(saved);
      setPatientListKey((k) => k + 1);
      setCart((prev) => ({ ...prev, patientId: saved.id }));
      setShowPatientForm(false);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "save failed");
    } finally {
      setFormBusy(false);
    }
  }

  async function findAnalogues(item: CartItem) {
    setOpenWarningFor(item.id);
    setAnalogueBusy(true);
    setAnalogueError(null);
    setAnalogues([]);
    setAnalogueUsedAi(false);
    setAnalogueRationaleUnavailable(false);
    setAnalogueVerdicts(new Map());
    setAnalogueCheckFailed(false);
    const line = resultsByRxcui.get(item.rxcui);
    const hasContraindication = (line?.warnings?.length ?? 0) > 0;
    const exclude =
      line?.exclude_ingredient ||
      line?.exclude_ingredient_name ||
      "1886";
    try {
      // With contraindications + Gemini key: UC-5 AI filter on Full list.
      // Without a key, analogue defaults use_ai=false; never force true (409).
      let useAi = false;
      if (hasContraindication) {
        try {
          const status = await apiFetch("analogue", "/analogues/ai-status");
          useAi = Boolean(status?.available);
        } catch {
          useAi = false;
        }
      }
      const data = await apiFetch(
        "analogue",
        `/analogues/${encodeURIComponent(item.rxcui)}?mode=full&use_ai=${useAi}&exclude_ingredient=${encodeURIComponent(exclude)}&facility_id=${facility.id}`,
      );
      const items: AnalogueHit[] = data.items ?? [];
      setAnalogueUsedAi(Boolean(data.use_ai));
      setAnalogueRationaleUnavailable(Boolean(data.rationale_unavailable));

      // Excluding one ingredient is not a safety check. These candidates have
      // been narrowed and ranked by stock, and nothing on that path has looked
      // at the patient — so assess them before offering one as a swap.
      const verdicts = await checkAnaloguesForPatient(item.rxcui, items);
      setAnalogueVerdicts(verdicts);
      // Ranked first, then cut — so the five shown are the best five, not
      // the first five the analogue service happened to return.
      setAnalogues(
        verdicts.size
          ? [...items].sort(bySafetyThenStock(verdicts)).slice(0, MAX_SUGGESTIONS)
          : items.slice(0, MAX_SUGGESTIONS),
      );
    } catch (err) {
      setAnalogueError(err instanceof Error ? err.message : "analogue search failed");
    } finally {
      setAnalogueBusy(false);
    }
  }

  /**
   * Assess analogue candidates against the selected patient.
   *
   * Failure here degrades to an unannotated list rather than losing the
   * analogues: the physician still gets the candidates they asked for, and the
   * UI says the safety check did not run instead of showing nothing and
   * implying there was nothing to show.
   */
  async function checkAnaloguesForPatient(
    replacing: string,
    items: AnalogueHit[],
  ): Promise<Map<string, AnalogueVerdict>> {
    const verdicts = new Map<string, AnalogueVerdict>();
    if (!cart.patientId || items.length === 0) return verdicts;
    try {
      const checked = await apiFetch("patients", "/analogue-check", {
        method: "POST",
        body: JSON.stringify({
          patient_id: cart.patientId,
          replacing,
          // Everything else the patient is on, so a substitute is judged by
          // what it adds rather than in isolation.
          regimen: cart.items
            .filter((i) => i.rxcui !== replacing)
            .map((i) => ({ rxcui: i.rxcui, name: i.name })),
          candidates: items.map((i) => ({ rxcui: i.rxcui, name: i.name })),
        }),
      });
      for (const row of checked?.results ?? []) verdicts.set(row.rxcui, row);
      if (checked?.sex) setPatientSex(String(checked.sex));
    } catch {
      setAnalogueCheckFailed(true);
    }
    return verdicts;
  }

  function replaceWithAnalogue(itemId: string, hit: AnalogueHit) {
    setCart((prev) => ({
      ...prev,
      items: prev.items.map((i) =>
        i.id === itemId ? { ...i, rxcui: hit.rxcui, name: hit.name } : i,
      ),
    }));
    setOpenWarningFor(null);
    setAnalogues([]);
  }

  function acceptPrescription() {
    if (!selectedPatient || cart.items.length === 0) return;
    setPrescription({
      patient: selectedPatient,
      items: [...cart.items],
      results: [...checkResults],
      at: new Date().toISOString(),
    });
    setCart({ patientId: null, items: [] });
    setCheckResults([]);
    setOpenWarningFor(null);
    setAnalogues([]);
  }

  if (user && user.role !== "physician" && user.role !== "admin") {
    return (
      <Callout tone="warning">
        Physician-only demo flow. Sign in as{" "}
        <span className="font-mono">ben@stmarys.org</span>.
      </Callout>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 lg:grid-cols-2">
        <Card size="sm">
          <CardHeader>
            <CardTitle>Drug search</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <form className="flex flex-wrap items-end gap-2" onSubmit={onSearch}>
              <div className="min-w-40 flex-1">
                <Label htmlFor="cart-drug-q" className="mb-1.5 text-xs text-muted-foreground">
                  Query
                </Label>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="cart-drug-q"
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="aspirin caffeine"
                    className="h-8 pl-8 text-xs"
                  />
                </div>
              </div>
              <Button type="submit" size="sm" className="h-8 text-xs" disabled={searchBusy || !query.trim()}>
                {searchBusy ? "Searching…" : "Search"}
              </Button>
            </form>
            {searchError && <p className="text-xs text-destructive">{searchError}</p>}
            <ul className="flex flex-col gap-2">
              {hits.map((hit) => (
                <li key={hit.rxcui} className="flex items-center justify-between gap-3 rounded-md border p-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{hit.name}</p>
                    <p className="font-mono text-[11px] text-muted-foreground">RxCUI {hit.rxcui}</p>
                  </div>
                  <Button type="button" size="sm" className="h-7 shrink-0 text-xs" onClick={() => addToCart(hit)}>
                    Add
                  </Button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card size="sm">
          <CardHeader>
            <CardTitle>Patient</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {patientsError && <p className="text-xs text-destructive">{patientsError}</p>}
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-40 flex-1">
                <Label htmlFor="patient-select" className="mb-1.5 text-xs text-muted-foreground">
                  Profile
                </Label>
                <PatientPicker
                  selected={selectedPatient}
                  refreshKey={patientListKey}
                  onError={setPatientsError}
                  onSelect={(picked) => {
                    // The picker returns the row it listed, which is the whole
                    // patient — no second fetch to show allergies below.
                    setSelectedPatient(picked);
                    setCart((prev) => ({ ...prev, patientId: picked?.id ?? null }));
                  }}
                />
              </div>
              <Button type="button" variant="outline" size="sm" className="h-8 text-xs" onClick={startCreatePatient}>
                New
              </Button>
              {selectedPatient && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 text-xs"
                  onClick={() => startEditPatient(selectedPatient)}
                >
                  Edit
                </Button>
              )}
            </div>
            {selectedPatient && (
              <p className="text-[11px] text-muted-foreground">
                Allergies: {selectedPatient.allergy_codes.join(", ") || "none"} · Conditions:{" "}
                {selectedPatient.condition_codes.join(", ") || "none"} · Blood:{" "}
                {selectedPatient.blood_group ?? "—"}
              </p>
            )}
            {/* On the card itself, so the shape of the risk is visible without
                anyone asking for it. Only once there is something to show: an
                empty body on a fresh cart reads as "checked and clear", which
                is not the same as "nothing assessed yet". */}
            {selectedPatient && regimenOrgans.length > 0 && (
              <div className="rounded-md border bg-muted/30 p-2">
                <AnatomyImpact
                  organs={regimenOrgans}
                  unmapped={regimenUnmapped}
                  sex={patientSex}
                  height={150}
                  dense
                />
              </div>
            )}
            {/* The window behind it carries the reasons and the per-drug
                breakdown -- detail the card has no room for. */}
            {selectedPatient && (
              <ImpactWindow
                patientName={selectedPatient.full_name}
                sex={patientSex}
                regimenOrgans={regimenOrgans}
                regimenUnmapped={regimenUnmapped}
                lines={checkResults}
                disabled={cart.items.length === 0 || checkBusy}
              />
            )}

            {showPatientForm && (
              <form className="flex flex-col gap-2 rounded-md border p-3" onSubmit={savePatient}>
                <h3 className="text-sm font-medium">{editingId ? "Edit patient" : "New patient"}</h3>
                <div>
                  <Label htmlFor="p-name" className="mb-1.5 text-xs text-muted-foreground">
                    Full name
                  </Label>
                  <Input
                    id="p-name"
                    type="text"
                    required
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    className="h-8 text-xs"
                  />
                </div>
                <div>
                  <Label htmlFor="p-dob" className="mb-1.5 text-xs text-muted-foreground">
                    Date of birth
                  </Label>
                  <Input
                    id="p-dob"
                    type="date"
                    required
                    value={formDob}
                    onChange={(e) => setFormDob(e.target.value)}
                    className="h-8 text-xs"
                  />
                </div>
                <div>
                  <Label htmlFor="p-blood" className="mb-1.5 text-xs text-muted-foreground">
                    Blood group
                  </Label>
                  <Select value={formBlood} onValueChange={setFormBlood}>
                    <SelectTrigger id="p-blood" size="sm" className="h-8 w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {["unknown", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((g) => (
                          <SelectItem key={g} value={g}>
                            {g}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="p-all" className="mb-1.5 text-xs text-muted-foreground">
                    Allergies (comma-separated)
                  </Label>
                  <Input
                    id="p-all"
                    type="text"
                    value={formAllergies}
                    onChange={(e) => setFormAllergies(e.target.value)}
                    placeholder="penicillin, caffeine"
                    className="h-8 text-xs"
                  />
                </div>
                <div>
                  <Label htmlFor="p-cond" className="mb-1.5 text-xs text-muted-foreground">
                    Conditions / avoid codes
                  </Label>
                  <Input
                    id="p-cond"
                    type="text"
                    value={formConditions}
                    onChange={(e) => setFormConditions(e.target.value)}
                    placeholder="avoid_caffeine"
                    className="h-8 text-xs"
                  />
                </div>
                {formError && <p className="text-xs text-destructive">{formError}</p>}
                <div className="flex gap-2">
                  <Button type="submit" size="sm" className="h-8 text-xs" disabled={formBusy}>
                    {formBusy ? "Saving…" : "Save"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8 text-xs"
                    onClick={() => setShowPatientForm(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            )}
          </CardContent>
        </Card>
      </div>

      <Card size="sm">
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Appointment cart</CardTitle>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={clearCart}
              disabled={cart.items.length === 0 && !cart.patientId}
            >
              Cancel / clear
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 text-xs"
              onClick={acceptPrescription}
              disabled={!selectedPatient || cart.items.length === 0}
            >
              Accept & generate prescription
            </Button>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!cart.patientId && cart.items.length > 0 && (
            <p className="text-xs text-muted-foreground">Select a patient to run contraindication checks.</p>
          )}
          {checkBusy && <p className="text-xs text-muted-foreground">Checking cart…</p>}
          {checkError && <p className="text-xs text-destructive">{checkError}</p>}
          {cart.items.length === 0 ? (
            <div className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
              Cart is empty.
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {cart.items.map((item) => {
                const line = resultsByRxcui.get(item.rxcui);
                const warnings = line?.warnings ?? [];
                const hasWarning = warnings.length > 0;
                const open = openWarningFor === item.id;
                return (
                  <li
                    key={item.id}
                    className={cn(
                      "rounded-md border p-2.5",
                      hasWarning && "border-amber-300 dark:border-amber-500/40",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{item.name}</p>
                        <p className="font-mono text-[11px] text-muted-foreground">RxCUI {item.rxcui}</p>
                        {line && (
                          <p className="text-[11px] text-muted-foreground">
                            verdict: {line.verdict}
                            {line.score != null ? ` · score ${line.score}` : ""}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                        {hasWarning && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-7 border-amber-300 text-xs text-amber-700 dark:border-amber-500/40 dark:text-amber-400"
                            onClick={() => (open ? setOpenWarningFor(null) : void findAnalogues(item))}
                          >
                            <TriangleAlert data-icon="inline-start" />
                            Warning ({warnings.length})
                          </Button>
                        )}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => removeFromCart(item.id)}
                        >
                          Remove
                        </Button>
                      </div>
                    </div>
                    {open && (
                      <div className="mt-3 flex flex-col gap-2 border-t pt-3">
                        <h3 className="text-sm font-medium">Why this warning</h3>
                        <ul className="flex flex-col gap-1.5 text-xs">
                          {warnings.map((w, idx) => (
                            <li key={`${w.code}-${idx}`}>
                              <span className="font-medium">{w.code}</span>: {w.message}{" "}
                              <span className="text-muted-foreground">({w.source})</span>
                            </li>
                          ))}
                        </ul>
                        {(line?.exclude_ingredient || line?.exclude_ingredient_name) && (
                          <p className="text-xs">
                            Suggested filter: exclude{" "}
                            <span className="font-mono">
                              {line.exclude_ingredient_name || line.exclude_ingredient}
                            </span>
                          </p>
                        )}
                        <Button
                          type="button"
                          size="sm"
                          className="h-8 w-fit text-xs"
                          onClick={() => void findAnalogues(item)}
                          disabled={analogueBusy}
                        >
                          {analogueBusy
                            ? "Finding analogues…"
                            : "Find analogues without this ingredient"}
                        </Button>
                        {analogueError && <p className="text-xs text-destructive">{analogueError}</p>}
                        {analogueUsedAi && !analogueRationaleUnavailable && (
                          <p className="text-[11px] text-muted-foreground">Gemini filtered this analogue list.</p>
                        )}
                        {analogueRationaleUnavailable && (
                          <p className="text-[11px] text-muted-foreground">
                            AI rationale unavailable — showing unfiltered Full list (still excluding the
                            avoided ingredient).
                          </p>
                        )}
                        {analogueCheckFailed && (
                          <p className="text-xs text-destructive">
                            These candidates could not be checked against the patient — they are
                            shown unranked, with no safety verdict.
                          </p>
                        )}
                        <ul className="flex flex-col gap-2">
                          {analogues.map((a) => {
                            const checked = analogueVerdicts.get(a.rxcui);
                            const blocked = checked?.verdict === "blocked";
                            return (
                            <li
                              key={a.rxcui}
                              className={cn(
                                "flex items-start justify-between gap-3 rounded-md border p-2.5",
                                blocked && "border-destructive/40 bg-destructive/5",
                              )}
                            >
                              <div className="min-w-0">
                                <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                                  {a.name}
                                  <span className="flex items-center gap-1.5 font-normal">
                                    <span className="text-xs text-muted-foreground">Shelf</span>
                                    <StockBand status={a.stock_status} quantity={a.quantity} />
                                  </span>
                                </p>
                                <p className="font-mono text-[11px] text-muted-foreground">RxCUI {a.rxcui}</p>
                                {a.availability ? (
                                  <p className="text-[11px] text-muted-foreground">
                                    {a.availability.quantity > 0
                                      ? `${a.availability.quantity} ${a.availability.unit} here`
                                      : "Not stocked here"}
                                    {a.availability.nearest_with_stock
                                      ? ` · nearest ${a.availability.nearest_with_stock.name} (${a.availability.nearest_with_stock.distance_km} km)`
                                      : ""}
                                  </p>
                                ) : null}
                                {checked ? (
                                  <p
                                    className={cn(
                                      "text-[11px]",
                                      blocked
                                        ? "font-medium text-destructive"
                                        : "text-muted-foreground",
                                    )}
                                  >
                                    for this patient: {checked.verdict}
                                    {checked.score != null ? ` · score ${checked.score}` : ""}
                                    {checked.findings.length
                                      ? ` · ${checked.findings.length} finding${checked.findings.length === 1 ? "" : "s"}`
                                      : ""}
                                  </p>
                                ) : (
                                  <p className="text-[11px] text-muted-foreground">
                                    not assessed for this patient
                                  </p>
                                )}
                                {/* The reasons behind a verdict, not just its colour — the
                                    same basis the cart line shows for the drug being replaced. */}
                                {/* Keyed by position as well as code: two risk factors
                                    from one label share a code, and a bare code would
                                    collide. */}
                                {checked?.findings.map((f, i) => (
                                  <p
                                    key={`${f.code}-${i}`}
                                    className="text-[11px] text-muted-foreground"
                                  >
                                    {f.severity}: {f.message}
                                  </p>
                                ))}
                                {/* The same findings on a torso. Ten codes make a
                                    physician assemble the anatomy themselves; the
                                    shading says "kidneys and liver" at a glance.
                                    Only rendered when the assessment produced organ
                                    findings — an empty body would read as "checked
                                    and clear", which is not the same as "no
                                    organ-specific finding". */}
                                {/* Stock, said plainly. "Safer but unavailable"
                                    is a different answer from "safer", and a
                                    physician choosing now needs to see which
                                    one they are being offered. */}
                                <p className="text-[11px]">
                                  {(a.quantity ?? 0) > 0 ? (
                                    <span className="text-emerald-700 dark:text-emerald-400">
                                      In stock here · {a.quantity}
                                    </span>
                                  ) : (
                                    <span className="text-muted-foreground">
                                      Not stocked at this facility
                                    </span>
                                  )}
                                </p>
                                {/* The reason this ranked where it did. An
                                    organ already loaded by another drug in the
                                    cart is the thing a verdict alone cannot
                                    tell you. */}
                                {checked?.compounds && checked.compounds.length > 0 && (
                                  <p className="text-[11px] text-amber-700 dark:text-amber-400">
                                    Adds to {checked.compounds.join(", ")} — already loaded by
                                    this patient&apos;s other drugs
                                  </p>
                                )}
                                {/* Where this substitute bears on the patient.
                                    Only rendered when the assessment produced
                                    organ findings — an empty figure would read
                                    as "checked and clear", which is not the
                                    same as "no organ-specific finding". */}
                                {checked?.organs && checked.organs.length > 0 && (
                                  <div className="mt-3 border-t pt-3">
                                    <AnatomyImpact
                                      organs={checked.organs}
                                      unmapped={checked.organs_unmapped ?? []}
                                      sex={patientSex}
                                      height={260}
                                    />
                                  </div>
                                )}
                                {a.reason && <p className="text-xs">{a.reason}</p>}
                                {a.citation && (
                                  <p className="text-[11px] text-muted-foreground">cite: {a.citation}</p>
                                )}
                              </div>
                              <Button
                                type="button"
                                size="sm"
                                variant={blocked ? "outline" : "default"}
                                className={cn(
                                  "h-7 shrink-0 text-xs",
                                  blocked && "border-destructive/40 text-destructive",
                                )}
                                onClick={() => replaceWithAnalogue(item.id, a)}
                              >
                                {/* Still clickable when blocked. /cart-check is warnings-only
                                    by design and the physician prescribes, not the tool —
                                    but the label should not pretend this is a routine swap. */}
                                {blocked ? "Replace anyway" : "Replace with analogue"}
                              </Button>
                            </li>
                            );
                          })}
                        </ul>
                        {!analogueBusy && analogues.length === 0 && !analogueError && (
                          <p className="text-xs text-muted-foreground">No analogues returned yet.</p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <Dialog open={prescription !== null} onOpenChange={(open) => !open && setPrescription(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Prescription generated</DialogTitle>
            <DialogDescription>
              {prescription ? new Date(prescription.at).toLocaleString() : ""}
            </DialogDescription>
          </DialogHeader>
          {prescription && (
            <div className="flex flex-col gap-2 text-sm">
              <p>
                <span className="font-medium">{prescription.patient.full_name}</span> · DOB{" "}
                {prescription.patient.date_of_birth} · Blood {prescription.patient.blood_group ?? "—"}
              </p>
              <ol className="list-decimal space-y-1 pl-4 text-xs">
                {prescription.items.map((item) => {
                  const line = prescription.results.find((r) => r.rxcui === item.rxcui);
                  return (
                    <li key={item.id}>
                      {item.name} (RxCUI {item.rxcui})
                      {line?.warnings?.length ? ` — ${line.warnings.length} warning(s) noted` : ""}
                    </li>
                  );
                })}
              </ol>
              <p className="text-[11px] text-muted-foreground">
                Demo only — not persisted. Cart is clear for the next patient.
              </p>
            </div>
          )}
          <DialogFooter>
            <Button type="button" size="sm" onClick={() => setPrescription(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
