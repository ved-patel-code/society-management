import { Bell } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useUnreadNotifications } from "@/hooks/useUnreadNotifications";

export function BellButton() {
  const navigate = useNavigate();
  const { count } = useUnreadNotifications();

  return (
    <Button
      variant="ghost"
      size="icon"
      className="relative"
      onClick={() => navigate("/notifications")}
      aria-label={count > 0 ? `${count} unread notifications` : "Notifications"}
    >
      <Bell className="h-5 w-5" />
      {count > 0 ? (
        <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground">
          {count > 99 ? "99+" : count}
        </span>
      ) : null}
    </Button>
  );
}
