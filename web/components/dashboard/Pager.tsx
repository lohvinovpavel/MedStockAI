"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Client-side pager for tables that fetch everything in one shot. Renders
// nothing when a single page suffices, so wiring it is free for small lists.
export function Pager({
  page,
  pageCount,
  onPage,
  className,
  children,
}: {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  className?: string;
  children?: React.ReactNode;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <span className="text-[11px] text-muted-foreground">{children}</span>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft className="size-3.5" /> Prev
        </Button>
        <span className="px-1 text-[11px] tabular-nums text-muted-foreground">
          {page} / {pageCount}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          Next <ChevronRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
