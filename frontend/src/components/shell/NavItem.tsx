import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

interface NavItemProps {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
  onNavigate?: () => void;
}

export function NavItem({ to, label, icon: Icon, badge, onNavigate }: NavItemProps) {
  return (
    <NavLink
      to={to}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-accent hover:text-foreground",
        )
      }
    >
      <Icon className="h-5 w-5 shrink-0" />
      <span className="flex-1">{label}</span>
      {badge && badge > 0 ? (
        <Badge variant="destructive" className="h-5 min-w-5 justify-center px-1.5">
          {badge > 99 ? "99+" : badge}
        </Badge>
      ) : null}
    </NavLink>
  );
}
