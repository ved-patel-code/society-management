import { Bell, Megaphone, Wallet, Wrench, type LucideIcon } from "lucide-react";

export interface NavEntry {
  module: string;
  label: string;
  route: string;
  icon: LucideIcon;
}

// Order is fixed by the foundation spec (§9).
export const NAV_ENTRIES: NavEntry[] = [
  { module: "notices", label: "Notice Board", route: "/notices", icon: Megaphone },
  { module: "finance", label: "Financial", route: "/finance", icon: Wallet },
  { module: "complaints", label: "Complaints", route: "/complaints", icon: Wrench },
  { module: "notifications", label: "Notifications", route: "/notifications", icon: Bell },
];

// Resolve a page title from the current pathname.
export function titleForPath(pathname: string): string {
  if (pathname === "/" || pathname === "") return "Society Management";
  const match = NAV_ENTRIES.find(
    (e) => pathname === e.route || pathname.startsWith(`${e.route}/`),
  );
  return match?.label ?? "Society Management";
}
