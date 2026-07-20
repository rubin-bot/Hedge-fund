"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType, ReactNode, SVGProps } from "react";

import { SimulatedBadge } from "./ui/badge";
import { AIIcon, ExecutionIcon, FactorIcon, PortfolioIcon, RiskIcon } from "./ui/icons";

interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

// Exactly 5 items: fits the ui-ux-pro-max ux-guidelines "bottom nav <= 5"
// rule with no trimming needed once this also becomes the mobile tab bar.
const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Portfolio", icon: PortfolioIcon },
  { href: "/factors", label: "Factor Research", icon: FactorIcon },
  { href: "/risk", label: "Risk Controls", icon: RiskIcon },
  { href: "/execution", label: "Execution Log", icon: ExecutionIcon },
  { href: "/ai", label: "AI Commentary", icon: AIIcon },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-card lg:flex">
        <div className="border-b border-border px-5 py-5">
          <span className="font-mono text-sm font-semibold tracking-tight">Long-Short Research</span>
        </div>
        <nav className="flex flex-1 flex-col gap-1 p-3">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.href} item={item} active={pathname === item.href} />
          ))}
        </nav>
        <div className="border-t border-border p-4">
          <SimulatedBadge />
        </div>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border bg-card px-4 py-3 lg:justify-end lg:px-6">
          <span className="font-mono text-sm font-semibold tracking-tight lg:hidden">Long-Short Research</span>
          <SimulatedBadge />
        </header>

        <main className="flex-1 px-4 py-5 pb-20 lg:px-8 lg:py-6 lg:pb-6">{children}</main>

        <nav className="fixed inset-x-0 bottom-0 z-10 flex border-t border-border bg-card lg:hidden">
          {NAV_ITEMS.map((item) => (
            <BottomTab key={item.href} item={item} active={pathname === item.href} />
          ))}
        </nav>
      </div>
    </div>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
        active ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
      }`}
    >
      <Icon className="h-4.5 w-4.5" />
      {item.label}
    </Link>
  );
}

function BottomTab({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={`flex flex-1 flex-col items-center gap-1 py-2.5 text-[11px] ${
        active ? "text-foreground" : "text-muted-foreground"
      }`}
    >
      <Icon className="h-5 w-5" />
      {item.label.split(" ")[0]}
    </Link>
  );
}
