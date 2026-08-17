"use client";

import { ThemeProvider as NextThemesProvider, type ThemeProviderProps } from "next-themes";

// Thin wrapper so app/layout.tsx (a server component) doesn't need "use
// client" just to mount this. next-themes reads/writes the .dark class
// itself — globals.css already defines `@custom-variant dark (&:is(.dark
// *))` and a full .dark token block; this is the one piece that was
// missing to make either do anything.
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
