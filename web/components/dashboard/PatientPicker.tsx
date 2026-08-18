"use client";

/**
 * Patient picker — a searchable combobox over a cohort too large to list.
 *
 * A plain `<Select>` worked while a demo environment held eight patients. It
 * stops working at a thousand: the physician is handed a dropdown they cannot
 * reach the bottom of, and the browser is handed a thousand names and dates of
 * birth to render one line. Both problems are the same problem, and the fix is
 * the same fix — ask the server for the few that match what was typed.
 *
 * The selected patient is held separately from the search results on purpose.
 * A bounded list means the current selection is usually *not* in it, and a
 * picker that blanks its own label the moment you type is worse than one that
 * cannot search at all.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

/** What the picker needs to render a row. Callers usually have more. */
export type PatientOption = {
  id: string;
  full_name: string;
  date_of_birth: string;
};

const PAGE = 30;

// Generic over the row shape: /patients returns the full record, and the cart
// renders allergies from the selection straight away. Narrowing to the three
// fields this component reads would force the caller to cast it back.
export function PatientPicker<T extends PatientOption>({
  selected,
  onSelect,
  onError,
  refreshKey = 0,
}: {
  selected: T | null;
  onSelect: (patient: T | null) => void;
  onError?: (message: string | null) => void;
  /** Bump to re-run the current search — after creating or editing a patient. */
  refreshKey?: number;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);

  const search = useCallback(
    async (term: string, signal: { cancelled: boolean }) => {
      setBusy(true);
      try {
        const params = new URLSearchParams({ limit: String(PAGE) });
        if (term) params.set("q", term);
        const data = await apiFetch("patients", `/patients?${params}`);
        if (signal.cancelled) return;
        setItems(data.items ?? []);
        setTotal(data.total ?? (data.items?.length ?? 0));
        onError?.(null);
      } catch (err) {
        if (signal.cancelled) return;
        setItems([]);
        setTotal(0);
        onError?.(err instanceof Error ? err.message : "failed to load patients");
      } finally {
        if (!signal.cancelled) setBusy(false);
      }
    },
    [onError],
  );

  // Debounced, and only while the popover is open — a closed picker has no
  // reason to be querying, and the cart page mounts this on every visit.
  useEffect(() => {
    if (!open) return;
    const signal = { cancelled: false };
    const timer = setTimeout(() => void search(query, signal), query ? 200 : 0);
    return () => {
      signal.cancelled = true;
      clearTimeout(timer);
    };
  }, [open, query, refreshKey, search]);

  const label = selected
    ? `${selected.full_name} (${selected.date_of_birth})`
    : "Select patient…";

  // cmdk filters its items client-side by default. Here the server has already
  // decided what matches, so filtering again would hide rows that matched on
  // something the local matcher scores differently.
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          id="patient-select"
          className="h-8 w-full justify-between text-xs font-normal"
        >
          <span className={cn("truncate", !selected && "text-muted-foreground")}>{label}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search by name…"
            value={query}
            onValueChange={setQuery}
            className="text-xs"
          />
          <CommandList>
            <CommandEmpty className="py-4 text-xs">
              {busy ? "Searching…" : "No patient matches that name."}
            </CommandEmpty>
            <CommandGroup>
              {selected && (
                <CommandItem
                  value="__clear__"
                  className="text-xs text-muted-foreground"
                  onSelect={() => {
                    onSelect(null);
                    setOpen(false);
                  }}
                >
                  Clear selection
                </CommandItem>
              )}
              {items.map((p) => (
                <CommandItem
                  key={p.id}
                  value={p.id}
                  className="text-xs"
                  onSelect={() => {
                    onSelect(p);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "size-3.5",
                      selected?.id === p.id ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <span className="truncate">{p.full_name}</span>
                  <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                    {p.date_of_birth}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
          {/* Say what is *not* shown. A list cut off at 30 that looks complete is
              how someone concludes a patient was never admitted. */}
          {total > items.length && (
            <p className="border-t px-3 py-2 text-[11px] text-muted-foreground">
              Showing {items.length} of {total}. Keep typing to narrow.
            </p>
          )}
        </Command>
      </PopoverContent>
    </Popover>
  );
}
