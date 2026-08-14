"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Plus,
  ChevronDown,
  Search,
  Package,
  TrendingUp,
  Globe,
  Bot,
  LogOut,
  Settings,
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
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useCopilot } from "@/lib/copilot-context";
import { inventory } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const FACILITIES = [
  "Clinic #1 (Central Hospital)",
  "Clinic #2 (Riverside Outpatient)",
  "Clinic #3 (West End Community)",
  "Regional Warehouse North",
];

const TABS = [
  { href: "/inventory", label: "Inventory & Batches", icon: Package },
  { href: "/forecasts", label: "Restock & Forecasts", icon: TrendingUp },
  { href: "/shortages", label: "Shortage Matrix", icon: Globe },
];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const { open: copilotOpen, toggle: toggleCopilot } = useCopilot();
  const [facility, setFacility] = useState(FACILITIES[0]);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  function goToSku() {
    setSearchOpen(false);
    router.push("/inventory");
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-3">
      <Link href="/inventory" className="flex items-center gap-2 pr-2">
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-4" strokeWidth={3} />
        </span>
        <span className="text-sm font-semibold tracking-tight">MedStock AI</span>
      </Link>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 gap-1 text-xs font-normal">
            {facility}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
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

      <nav className="ml-1 flex items-center gap-1">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
                active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-56 justify-start gap-2 text-xs text-muted-foreground font-normal"
          onClick={() => setSearchOpen(true)}
        >
          <Search className="size-3.5" />
          Search SKUs, batches…
          <kbd className="ml-auto rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">⌘K</kbd>
        </Button>

        <Button
          variant={copilotOpen ? "secondary" : "outline"}
          size="icon"
          className="size-8"
          onClick={toggleCopilot}
          aria-pressed={copilotOpen}
          aria-label="Toggle AI Copilot"
        >
          <Bot className="size-4" />
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 rounded-full">
              <Avatar className="size-8">
                <AvatarFallback className="text-xs">CP</AvatarFallback>
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>
              <p className="text-sm font-medium">Dr. Casey Park</p>
              <p className="text-xs font-normal text-muted-foreground">Chief Pharmacist</p>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
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
      </div>

      <CommandDialog open={searchOpen} onOpenChange={setSearchOpen} title="Search" description="Search SKUs and batches">
        <CommandInput placeholder="Search drug name, INN, batch #, ATC code…" />
        <CommandList>
          <CommandEmpty>No results.</CommandEmpty>
          <CommandGroup heading="SKUs">
            {inventory.slice(0, 8).map((item) => (
              <CommandItem key={item.id} onSelect={goToSku}>
                {item.drugName}
                <span className="ml-auto text-xs text-muted-foreground">{item.batchNumber}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </header>
  );
}
