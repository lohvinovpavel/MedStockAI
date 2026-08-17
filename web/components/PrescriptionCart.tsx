"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import { useSession } from "@/lib/session";

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
};

type AnalogueHit = {
  rxcui: string;
  name: string;
  quantity?: number;
  stock_status?: string;
  reason?: string;
  citation?: string;
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
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<DrugHit[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [cart, setCart] = useState<CartState>({ patientId: null, items: [] });
  const [hydrated, setHydrated] = useState(false);

  const [patients, setPatients] = useState<Patient[]>([]);
  const [patientsError, setPatientsError] = useState<string | null>(null);
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

  const selectedPatient = useMemo(
    () => patients.find((p) => p.id === cart.patientId) ?? null,
    [patients, cart.patientId],
  );

  const resultsByRxcui = useMemo(() => {
    const map = new Map<string, CartLineResult>();
    for (const row of checkResults) map.set(row.rxcui, row);
    return map;
  }, [checkResults]);

  const refreshPatients = useCallback(async () => {
    setPatientsError(null);
    try {
      const data = await apiFetch("patients", "/patients");
      setPatients(data.items ?? []);
    } catch (err) {
      setPatientsError(err instanceof Error ? err.message : "failed to load patients");
    }
  }, []);

  useEffect(() => {
    if (user) void refreshPatients();
  }, [user, refreshPatients]);

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
      await refreshPatients();
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
        `/analogues/${encodeURIComponent(item.rxcui)}?mode=full&use_ai=${useAi}&exclude_ingredient=${encodeURIComponent(exclude)}`,
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
      <div className="prescribe">
        <p>
          Physician-only demo flow. Sign in as <code>ben@stmarys.org</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="prescribe">
      <p className="lede">
        Search drugs, add them to the appointment cart, select a patient profile,
        review warnings, replace with analogues, then accept to generate a
        prescription summary. Cart lives in this browser tab only.
      </p>

      <section className="prescribe-grid">
        <div>
          <h2>Drug search</h2>
          <form className="row" onSubmit={onSearch}>
            <div>
              <label htmlFor="cart-drug-q">Query</label>
              <input
                id="cart-drug-q"
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="aspirin caffeine"
              />
            </div>
            <button type="submit" disabled={searchBusy || !query.trim()}>
              {searchBusy ? "Searching…" : "Search"}
            </button>
          </form>
          {searchError && <p className="error">{searchError}</p>}
          <ul className="result-list">
            {hits.map((hit) => (
              <li key={hit.rxcui}>
                <div className="hit-row">
                  <div>
                    <strong>{hit.name}</strong>
                    <div className="muted">RxCUI {hit.rxcui}</div>
                  </div>
                  <button type="button" onClick={() => addToCart(hit)}>
                    Add
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h2>Patient</h2>
          {patientsError && <p className="error">{patientsError}</p>}
          <div className="row">
            <div>
              <label htmlFor="patient-select">Profile</label>
              <select
                id="patient-select"
                value={cart.patientId ?? ""}
                onChange={(e) =>
                  setCart((prev) => ({
                    ...prev,
                    patientId: e.target.value || null,
                  }))
                }
              >
                <option value="">Select patient…</option>
                {patients.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name} ({p.date_of_birth})
                  </option>
                ))}
              </select>
            </div>
            <button type="button" className="secondary" onClick={startCreatePatient}>
              New
            </button>
            {selectedPatient && (
              <button
                type="button"
                className="secondary"
                onClick={() => startEditPatient(selectedPatient)}
              >
                Edit
              </button>
            )}
          </div>
          {selectedPatient && (
            <p className="muted small">
              Allergies: {selectedPatient.allergy_codes.join(", ") || "none"} ·
              Conditions: {selectedPatient.condition_codes.join(", ") || "none"} ·
              Blood: {selectedPatient.blood_group ?? "—"}
            </p>
          )}

          {showPatientForm && (
            <form className="patient-form" onSubmit={savePatient}>
              <h3>{editingId ? "Edit patient" : "New patient"}</h3>
              <label htmlFor="p-name">Full name</label>
              <input
                id="p-name"
                type="text"
                required
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
              />
              <label htmlFor="p-dob">Date of birth</label>
              <input
                id="p-dob"
                type="date"
                required
                value={formDob}
                onChange={(e) => setFormDob(e.target.value)}
              />
              <label htmlFor="p-blood">Blood group</label>
              <select
                id="p-blood"
                value={formBlood}
                onChange={(e) => setFormBlood(e.target.value)}
              >
                {["unknown", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
              <label htmlFor="p-all">Allergies (comma-separated)</label>
              <input
                id="p-all"
                type="text"
                value={formAllergies}
                onChange={(e) => setFormAllergies(e.target.value)}
                placeholder="penicillin, caffeine"
              />
              <label htmlFor="p-cond">Conditions / avoid codes</label>
              <input
                id="p-cond"
                type="text"
                value={formConditions}
                onChange={(e) => setFormConditions(e.target.value)}
                placeholder="avoid_caffeine"
              />
              {formError && <p className="error">{formError}</p>}
              <div className="row">
                <button type="submit" disabled={formBusy}>
                  {formBusy ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowPatientForm(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </section>

      <section>
        <div className="cart-header">
          <h2>Appointment cart</h2>
          <div className="row cart-actions">
            <button
              type="button"
              className="secondary"
              onClick={clearCart}
              disabled={cart.items.length === 0 && !cart.patientId}
            >
              Cancel / clear
            </button>
            <button
              type="button"
              onClick={acceptPrescription}
              disabled={!selectedPatient || cart.items.length === 0}
            >
              Accept &amp; generate prescription
            </button>
          </div>
        </div>
        {!cart.patientId && cart.items.length > 0 && (
          <p className="muted">Select a patient to run contraindication checks.</p>
        )}
        {checkBusy && <p className="muted">Checking cart…</p>}
        {checkError && <p className="error">{checkError}</p>}
        {cart.items.length === 0 ? (
          <p className="muted">Cart is empty.</p>
        ) : (
          <ul className="cart-list">
            {cart.items.map((item) => {
              const line = resultsByRxcui.get(item.rxcui);
              const warnings = line?.warnings ?? [];
              const hasWarning = warnings.length > 0;
              const open = openWarningFor === item.id;
              return (
                <li key={item.id} className={hasWarning ? "has-warning" : undefined}>
                  <div className="cart-line">
                    <div>
                      <strong>{item.name}</strong>
                      <div className="muted">RxCUI {item.rxcui}</div>
                      {line && (
                        <div className="muted small">
                          verdict: {line.verdict}
                          {line.score != null ? ` · score ${line.score}` : ""}
                        </div>
                      )}
                    </div>
                    <div className="cart-line-actions">
                      {hasWarning && (
                        <button
                          type="button"
                          className="warn-btn"
                          onClick={() =>
                            open ? setOpenWarningFor(null) : void findAnalogues(item)
                          }
                        >
                          Warning ({warnings.length})
                        </button>
                      )}
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => removeFromCart(item.id)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                  {open && (
                    <div className="warning-panel">
                      <h3>Why this warning</h3>
                      <ul>
                        {warnings.map((w, idx) => (
                          <li key={`${w.code}-${idx}`}>
                            <strong>{w.code}</strong>: {w.message}
                            <span className="muted"> ({w.source})</span>
                          </li>
                        ))}
                      </ul>
                      {(line?.exclude_ingredient || line?.exclude_ingredient_name) && (
                        <p>
                          Suggested filter: exclude{" "}
                          <code>
                            {line.exclude_ingredient_name || line.exclude_ingredient}
                          </code>
                        </p>
                      )}
                      <button
                        type="button"
                        onClick={() => void findAnalogues(item)}
                        disabled={analogueBusy}
                      >
                        {analogueBusy
                          ? "Finding analogues…"
                          : "Find analogues without this ingredient"}
                      </button>
                      {analogueError && <p className="error">{analogueError}</p>}
                      {analogueUsedAi && !analogueRationaleUnavailable && (
                        <p className="muted small">Gemini filtered this analogue list.</p>
                      )}
                      {analogueRationaleUnavailable && (
                        <p className="muted small">
                          AI rationale unavailable — showing unfiltered Full list
                          (still excluding the avoided ingredient).
                        </p>
                      )}
                      <ul className="result-list analogue-inline">
                        {analogues.map((a) => (
                          <li key={a.rxcui}>
                            <div className="hit-row">
                              <div>
                                <strong>{a.name}</strong>
                                <div className="muted">RxCUI {a.rxcui}</div>
                                {a.reason && (
                                  <div className="small">{a.reason}</div>
                                )}
                                {a.citation && (
                                  <div className="muted small">
                                    cite: {a.citation}
                                  </div>
                                )}
                              </div>
                              <button
                                type="button"
                                onClick={() => replaceWithAnalogue(item.id, a)}
                              >
                                Replace with analogue
                              </button>
                            </div>
                          </li>
                        ))}
                      </ul>
                      {!analogueBusy && analogues.length === 0 && !analogueError && (
                        <p className="muted">No analogues returned yet.</p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {prescription && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h2>Prescription generated</h2>
            <p className="muted small">{new Date(prescription.at).toLocaleString()}</p>
            <p>
              <strong>{prescription.patient.full_name}</strong> · DOB{" "}
              {prescription.patient.date_of_birth} · Blood{" "}
              {prescription.patient.blood_group ?? "—"}
            </p>
            <ol>
              {prescription.items.map((item) => {
                const line = prescription.results.find((r) => r.rxcui === item.rxcui);
                return (
                  <li key={item.id}>
                    {item.name} (RxCUI {item.rxcui})
                    {line?.warnings?.length
                      ? ` — ${line.warnings.length} warning(s) noted`
                      : ""}
                  </li>
                );
              })}
            </ol>
            <p className="muted">
              Demo only — not persisted. Cart is clear for the next patient.
            </p>
            <button type="button" onClick={() => setPrescription(null)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
