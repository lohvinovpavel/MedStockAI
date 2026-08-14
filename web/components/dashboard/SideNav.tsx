"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown, Globe, LogOut, Package, Plus, ScrollText, Settings, TrendingUp, User as UserIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/inventory", label: "Inventory & Batches", icon: Package },
  { href: "/forecasts", label: "Restock & Forecasts", icon: TrendingUp },
  { href: "/shortages", label: "Shortage Matrix", icon: Globe },
  { href: "/audit", label: "Audit Log", icon: ScrollText },
];

const FACILITIES = [
  "Clinic #1 (Central Hospital)",
  "Clinic #2 (Riverside Outpatient)",
  "Clinic #3 (West End Community)",
  "Regional Warehouse North",
];

// The whole dashboard shell now lives here: brand + route tabs up top,
// facility switcher and user menu pinned to the bottom. There's no header
// bar anymore — search lives on the Inventory page and the AI Copilot has
// its own open/collapse control in the drawer, so nothing else needed one.
export function SideNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [facility, setFacility] = useState(FACILITIES[0]);

  return (
    <nav className="flex w-52 shrink-0 flex-col border-r bg-card p-2">
      <Link href="/inventory" className="mb-2 flex items-center gap-2 px-2 py-1.5">
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-4" strokeWidth={3} />
        </span>
        <span className="text-sm font-semibold tracking-tight">MedStock AI</span>
      </Link>

      <div className="flex flex-1 flex-col gap-1">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium transition-colors",
                active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          );
        })}
      </div>

      <Separator className="my-2" />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 w-full justify-between gap-1 text-xs font-normal">
            <span className="truncate">{facility}</span>
            <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>Switch facility</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {FACILITIES.map((f) => (
              <DropdownMenuItem key={f} onSelect={() => setFacility(f)}>
                {f}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="mt-1 h-11 w-full justify-start gap-2 px-2 text-xs font-normal">
            <Avatar className="size-7 shrink-0">
              <AvatarFallback className="text-xs">CP</AvatarFallback>
            </Avatar>
            <span className="flex min-w-0 flex-col items-start leading-tight">
              <span className="truncate font-medium text-foreground">Dr. Casey Park</span>
              <span className="truncate text-[11px] text-muted-foreground">Chief Pharmacist</span>
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuGroup>
            <DropdownMenuItem>
              <UserIcon /> Profile
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Settings /> Settings
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => router.push("/login")}>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  );
}
