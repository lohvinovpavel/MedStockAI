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
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn(
          "h-full w-full flex-1 bg-primary transition-transform",
          value == null && "w-1/3 animate-[progress-indeterminate_1.1s_ease-in-out_infinite]",
        )}
        style={value != null ? { transform: `translateX(-${100 - value}%)` } : undefined}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
