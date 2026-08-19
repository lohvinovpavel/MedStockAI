"use client"

import * as React from "react"
import { Progress as ProgressPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

// `value` omitted/undefined renders Radix's indeterminate state — used here
// for "this is running, we don't know how long" (searches, tool calls)
// rather than a real percentage.
function Progress({
  className,
  value,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root>) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={value}
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-muted/70", className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn(
          "h-full w-full flex-1 rounded-full bg-gradient-to-r from-primary/70 to-primary shadow-[0_0_6px_-1px_var(--primary)] transition-transform",
          value == null && "w-2/5 animate-[progress-indeterminate_1.3s_ease-in-out_infinite]",
        )}
        style={value != null ? { transform: `translateX(-${100 - value}%)` } : undefined}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
