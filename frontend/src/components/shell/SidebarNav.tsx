import { IfModule } from "@/components/common/IfModule";
import { NavItem } from "@/components/shell/NavItem";
import { NAV_ENTRIES } from "@/components/shell/navConfig";
import { useUnreadNotifications } from "@/hooks/useUnreadNotifications";

interface SidebarNavProps {
  onNavigate?: () => void;
}

export function SidebarNav({ onNavigate }: SidebarNavProps) {
  const { count } = useUnreadNotifications();

  return (
    <nav className="flex flex-col gap-1 px-3">
      {NAV_ENTRIES.map((entry) => (
        <IfModule key={entry.module} module={entry.module}>
          <NavItem
            to={entry.route}
            label={entry.label}
            icon={entry.icon}
            badge={entry.module === "notifications" ? count : undefined}
            onNavigate={onNavigate}
          />
        </IfModule>
      ))}
    </nav>
  );
}
