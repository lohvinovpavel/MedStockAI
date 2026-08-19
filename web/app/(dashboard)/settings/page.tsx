"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const THEME_OPTIONS = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
] as const;

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  // Same hydration guard as SideNav's ThemeToggle — next-themes doesn't know
  // the resolved theme until after mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Settings</h1>
        <p className="text-xs text-muted-foreground">Preferences for this browser.</p>
      </div>

      <Card className="max-w-md gap-3 py-4">
        <CardHeader className="px-4">
          <CardTitle className="text-sm">Appearance</CardTitle>
          <CardDescription className="text-xs">Choose how MedStock AI looks on this device.</CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2 px-4">
          {THEME_OPTIONS.map(({ value, icon: Icon, label }) => (
            <Button
              key={value}
              variant={mounted && theme === value ? "secondary" : "outline"}
              size="sm"
              className="h-8 gap-1.5 text-xs"
              onClick={() => setTheme(value)}
              aria-pressed={mounted && theme === value}
            >
              <Icon className={cn("size-3.5")} />
              {label}
            </Button>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
