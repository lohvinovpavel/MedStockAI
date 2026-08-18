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
import { useFacility } from "@/lib/facility-context";
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

  const [prescription, setPrescription] = useState<PrescriptionSnapshot | null>(null);

  useEffect(() => {
    setCart(loadCart());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    saveCart(cart);
  }, [cart, hydrated]);

  const resultsByRxcui = useMemo(() => {
    const map = new Map<string, CartLineResult>();
    for (const row of checkResults) map.set(row.rxcui, row);
    return map;
  }, [checkResults]);

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
        if (!cancelled) setCheckResults(data.results ?? []);
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
      setAnalogues(data.items ?? []);
      setAnalogueUsedAi(Boolean(data.use_ai));
      setAnalogueRationaleUnavailable(Boolean(data.rationale_unavailable));
    } catch (err) {
      setAnalogueError(err instanceof Error ? err.message : "analogue search failed");
    } finally {
      setAnalogueBusy(false);
    }
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
                        <ul className="flex flex-col gap-2">
                          {analogues.map((a) => (
                            <li key={a.rxcui} className="flex items-start justify-between gap-3 rounded-md border p-2.5">
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
                                {a.reason && <p className="text-xs">{a.reason}</p>}
                                {a.citation && (
                                  <p className="text-[11px] text-muted-foreground">cite: {a.citation}</p>
                                )}
                              </div>
                              <Button
                                type="button"
                                size="sm"
                                className="h-7 shrink-0 text-xs"
                                onClick={() => replaceWithAnalogue(item.id, a)}
                              >
                                Replace with analogue
                              </Button>
                            </li>
                          ))}
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
