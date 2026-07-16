import { ChevronsUpDown, LogOut, Repeat } from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useAuth } from "@/hooks/useAuth";

function initials(name: string | null, email: string): string {
  const base = name?.trim() || email;
  const parts = base.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

export function UserMenu() {
  const { me, availablePortals, logout } = useAuth();
  const navigate = useNavigate();

  if (!me) return null;

  const name = me.user.full_name;
  const email = me.user.email;
  const canSwitch = availablePortals.length > 1;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full items-center gap-3 rounded-md p-2 text-left text-sm transition-colors hover:bg-accent">
        <Avatar className="h-8 w-8">
          <AvatarFallback>{initials(name, email)}</AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{name ?? email}</p>
          <p className="truncate text-xs text-muted-foreground">{email}</p>
        </div>
        <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="truncate">{name ?? email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {canSwitch ? (
          <DropdownMenuItem onSelect={() => navigate("/choose-portal")}>
            <Repeat className="mr-2 h-4 w-4" />
            Switch portal
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut className="mr-2 h-4 w-4" />
          Logout
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
