"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Bot,
  ChevronDown,
  ClipboardList,
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
  Warehouse,
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
import { useSession } from "@/lib/session";
import { canAccessPage, ROLE_LABEL } from "@/lib/rbac";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/inventory", label: "Inventory & Batches", icon: Package },
  { href: "/analogue", label: "Analogues", icon: Repeat2 },
  { href: "/forecasts", label: "Restock & Forecasts", icon: TrendingUp },
  { href: "/warehouse", label: "Warehouse", icon: Warehouse },
  { href: "/orders", label: "Purchase & Orders", icon: ShoppingCart },
  { href: "/shortages", label: "Shortage Matrix", icon: Globe },
  // PP-5. Sits beside Audit Log because it is the same kind of thing: a record
  // of who decided what, and on what basis.
  { href: "/prognosis", label: "Prognosis Review", icon: ClipboardList },
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
function initials(name: string) {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[parts.length - 1]?.[0] ?? "")).toUpperCase();
}

function NavBody({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const { facilityId, setFacilityId, facility, operatedFacilities } = useFacility();
  const { draftCount } = useOrders();
  const { user } = useSession();
  const tabs = TABS.filter((t) => canAccessPage(user?.role, t.href));

  return (
    <>
      <Link href="/inventory" onClick={onNavigate} className="mb-3 flex items-center gap-2.5 px-2 py-2">
        <span className="flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-none">
          <Plus className="size-4" strokeWidth={3} />
        </span>
        <div className="flex flex-col">
          <span className="text-sm font-semibold tracking-tight text-foreground">MedStock AI</span>
          <span className="text-[10px] text-muted-foreground -mt-0.5 tracking-wider uppercase font-medium">Data Observatory</span>
        </div>
      </Link>

      <div className="flex flex-1 flex-col gap-1">
        {tabs.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          const badge = href === "/orders" && draftCount > 0 ? draftCount : null;
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium tracking-[0.008em] transition-colors",
                active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" />
              <span className="truncate">{label}</span>
              {badge && (
                <span
                  className={cn(
                    "ml-auto flex min-w-4 items-center justify-center rounded-full px-1.5 font-sans text-[10px] font-medium tabular-nums",
                    active ? "bg-white/20 text-white" : "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300",
                  )}
                >
                  {badge}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      <Separator className="my-2 bg-border" />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 w-full justify-between gap-1 text-xs font-normal border-border bg-card">
            <span className="truncate">{facility.name}</span>
            <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="sr-only">Switch facility, currently {facility.name}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56 rounded-xl border-border bg-card">
          <DropdownMenuLabel className="text-xs text-muted-foreground">Switch facility</DropdownMenuLabel>
          <DropdownMenuSeparator className="bg-border" />
          <DropdownMenuGroup>
            {operatedFacilities.map((f) => (
              <DropdownMenuItem
                key={f.code}
                onSelect={() => setFacilityId(f.code)}
                className={cn(f.code === facilityId && "bg-muted font-medium")}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">{f.name}</span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {f.type}
                    {f.distanceKm != null && f.distanceKm > 0
                      ? ` · ${f.distanceKm}km`
                      : f.distanceKm === 0
                        ? " · this site"
                        : ""}
                  </span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="mt-1 h-11 w-full justify-start gap-2.5 px-2 text-xs font-normal hover:bg-muted">
            <Avatar className="size-7 shrink-0 border border-border">
              <AvatarFallback className="text-xs bg-muted text-foreground">{user?.full_name ? initials(user.full_name) : "?"}</AvatarFallback>
            </Avatar>
            <span className="flex min-w-0 flex-col items-start leading-tight">
              <span className="truncate font-medium text-foreground">{user?.full_name ?? "—"}</span>
              <span className="truncate text-[11px] text-muted-foreground">
                {user ? ROLE_LABEL[user.role as keyof typeof ROLE_LABEL] ?? user.role : ""}
              </span>
            </span>
            <span className="sr-only">Account menu, {user?.full_name ?? "unknown user"}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56 rounded-xl border-border bg-card">
          <DropdownMenuGroup>
            <DropdownMenuItem>
              <UserIcon /> Profile
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Settings /> Settings
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator className="bg-border" />
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

export function SideNav() {
  return (
    <nav className="hidden w-56 shrink-0 flex-col border-r border-border bg-card p-3 lg:flex">
      <NavBody />
    </nav>
  );
}

export function MobileTopBar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { setOpen: setCopilotOpen } = useCopilot();

  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border bg-card px-3 lg:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Open navigation menu">
            <Menu />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="flex w-64 flex-col gap-0 p-3 border-r border-border bg-card">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <NavBody onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>

      <Link href="/inventory" className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-3.5" strokeWidth={3} />
        </span>
        <span className="text-sm font-semibold tracking-tight text-foreground">MedStock AI</span>
      </Link>

      <Button variant="ghost" size="icon" onClick={() => setCopilotOpen(true)} aria-label="Open AI MedStock Assistant">
        <Bot />
      </Button>
    </div>
  );
}
