import { Menu } from "lucide-react";
import { useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { BellButton } from "@/components/shell/BellButton";
import { ThemeToggle } from "@/components/shell/ThemeToggle";
import { UserMenu } from "@/components/shell/UserMenu";
import { IfModule } from "@/components/common/IfModule";
import { titleForPath } from "@/components/shell/navConfig";

interface TopbarProps {
  onOpenSidebar: () => void;
}

export function Topbar({ onOpenSidebar }: TopbarProps) {
  const location = useLocation();
  const title = titleForPath(location.pathname);

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b bg-background px-4">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onOpenSidebar}
        aria-label="Open menu"
      >
        <Menu className="h-5 w-5" />
      </Button>

      <h1 className="flex-1 truncate text-lg font-semibold">{title}</h1>

      <div className="flex items-center gap-1">
        <IfModule module="notifications">
          <BellButton />
        </IfModule>
        <ThemeToggle />
        {/* Account menu (Logout / Switch portal) — always reachable, incl. mobile. */}
        <UserMenu variant="topbar" />
      </div>
    </header>
  );
}
