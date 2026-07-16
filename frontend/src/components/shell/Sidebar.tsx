import { Building2 } from "lucide-react";
import { SidebarNav } from "@/components/shell/SidebarNav";
import { UserMenu } from "@/components/shell/UserMenu";

interface SidebarProps {
  onNavigate?: () => void;
}

export function Sidebar({ onNavigate }: SidebarProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-5 py-5">
        <Building2 className="h-6 w-6 text-primary" />
        <span className="text-base font-semibold tracking-tight">
          Society Management
        </span>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        <SidebarNav onNavigate={onNavigate} />
      </div>
      <div className="border-t p-3">
        <UserMenu />
      </div>
    </div>
  );
}
