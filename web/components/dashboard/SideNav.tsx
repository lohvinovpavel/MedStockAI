"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Bot,
  ChevronDown,
  Globe,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Package,
  Plus,
  Repeat2,
  ScrollText,
  Settings,
  ShoppingCart,
  Sun,
  TrendingUp,
  User as UserIcon,
} from "lucide-react";
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
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
import { operatedFacilities } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/inventory", label: "Inventory & Batches", icon: Package },
  { href: "/analogue", label: "Analogues", icon: Repeat2 },
  { href: "/forecasts", label: "Restock & Forecasts", icon: TrendingUp },
  { href: "/orders", label: "Purchase & Orders", icon: ShoppingCart },
  { href: "/shortages", label: "Shortage Matrix", icon: Globe },
  { href: "/audit", label: "Audit Log", icon: ScrollText },
];

const THEME_OPTIONS = [
  { value: "light", icon: Sun, label: "Light theme" },
  { value: "dark", icon: Moon, label: "Dark theme" },
  { value: "system", icon: Monitor, label: "Match system theme" },
] as const;

// Mounted state guards the first paint: next-themes doesn't know the
// resolved theme until after hydration, so rendering `theme` any earlier
// would flip the pressed icon the instant the client catches up.
function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div className="flex items-center gap-0.5 rounded-md border p-0.5" role="group" aria-label="Theme">
      {THEME_OPTIONS.map(({ value, icon: Icon, label }) => (
        <Button
          key={value}
          variant={mounted && theme === value ? "secondary" : "ghost"}
          size="icon"
          className="size-7"
          onClick={() => setTheme(value)}
          aria-label={label}
          aria-pressed={mounted && theme === value}
        >
          <Icon className="size-3.5" />
        </Button>
      ))}
    </div>
  );
}

// Shared between the desktop inline nav and the mobile Sheet — no local
// state of its own, so mounting it in both places at once is safe (unlike
// the copilot drawer, which holds a live conversation).
function NavBody({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const { facilityId, setFacilityId, facility } = useFacility();
  const { draftCount } = useOrders();

  return (
    <>
      <Link href="/inventory" onClick={onNavigate} className="mb-2 flex items-center gap-2 px-2 py-1.5">
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-4" strokeWidth={3} />
        </span>
        <span className="text-sm font-semibold tracking-tight">MedStock AI</span>
      </Link>

      <div className="flex flex-1 flex-col gap-1">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          // Draft orders awaiting review — makes the forecast → orders
          // handoff visible the moment a suggestion is accepted.
          const badge = href === "/orders" && draftCount > 0 ? draftCount : null;
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium transition-colors",
                active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
              {badge && (
                <span
                  className={cn(
                    "ml-auto flex min-w-4 items-center justify-center rounded-full px-1 font-mono text-[10px] tabular-nums",
                    active ? "bg-primary-foreground/20 text-primary-foreground" : "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                  )}
                >
                  {badge}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      <Separator className="my-2" />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 w-full justify-between gap-1 text-xs font-normal">
            <span className="truncate">{facility.name}</span>
            <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="sr-only">Switch facility, currently {facility.name}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>Switch facility</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {operatedFacilities.map((f) => (
              <DropdownMenuItem
                key={f.id}
                onSelect={() => setFacilityId(f.id)}
                className={cn(f.id === facilityId && "bg-accent")}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">{f.name}</span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {f.type}
                    {f.distanceKm > 0 ? ` · ${f.distanceKm}km` : " · primary site"}
                  </span>
                </span>
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
            <span className="sr-only">Account menu, Dr. Casey Park</span>
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

      <div className="mt-1 flex items-center justify-between gap-2 px-1">
        <span className="text-[11px] text-muted-foreground">Theme</span>
        <ThemeToggle />
      </div>
    </>
  );
}

// Desktop-only inline sidebar. Below `lg` it renders nothing — MobileTopBar
// covers navigation there via a Sheet instead, since there's no room for a
// fixed 208px column at phone/tablet widths.
export function SideNav() {
  return (
    <nav className="hidden w-52 shrink-0 flex-col border-r bg-card p-2 lg:flex">
      <NavBody />
    </nav>
  );
}

// Below `lg`: a slim bar above the nav/main/copilot row carrying the brand,
// a hamburger that opens the same NavBody in a Sheet, and a shortcut to
// open the AI Copilot (which supplies its own trigger at `lg` and above).
export function MobileTopBar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { setOpen: setCopilotOpen } = useCopilot();

  // Close the nav sheet on navigation rather than leaving it open behind
  // the new page.
  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b bg-card px-2 lg:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Open navigation menu">
            <Menu />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="flex w-64 flex-col gap-0 p-2">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <NavBody onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>

      <Link href="/inventory" className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-3.5" strokeWidth={3} />
        </span>
        <span className="text-sm font-semibold tracking-tight">MedStock AI</span>
      </Link>

      <Button variant="ghost" size="icon" onClick={() => setCopilotOpen(true)} aria-label="Open AI Copilot">
        <Bot />
      </Button>
    </div>
  );
}
