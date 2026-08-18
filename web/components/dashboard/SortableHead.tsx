"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export type SortDirection = "asc" | "desc";
export type SortState<K extends string> = { key: K; direction: SortDirection } | null;

// Three-state cycle per column — unsorted -> asc -> desc -> unsorted — shared
// by Inventory and Order History so both tables get the same click target,
// icon, and aria-sort wiring instead of two hand-rolled copies.
export function nextSortState<K extends string>(current: SortState<K>, key: K): SortState<K> {
  if (current?.key !== key) return { key, direction: "asc" };
  if (current.direction === "asc") return { key, direction: "desc" };
  return null;
}

// Comparable value a column sorts by — string/number covers every column in
// both tables (dates and money already reduce to one of these at the call
// site) without needing a Date-specific branch here.
export function compareValues(a: string | number, b: string | number): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

export function SortableHead<K extends string>({
  sortKey,
  sort,
  onSort,
  className,
  children,
}: {
  sortKey: K;
  sort: SortState<K>;
  onSort: (key: K) => void;
  className?: string;
  children: React.ReactNode;
}) {
  const active = sort?.key === sortKey;
  const Icon = active ? (sort.direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <TableHead className={cn("whitespace-nowrap", className)} aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {children}
        <Icon className={cn("size-3.5", active ? "text-foreground" : "text-muted-foreground/50")} />
      </button>
    </TableHead>
  );
}
