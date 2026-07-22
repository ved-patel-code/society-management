import { useNavigate } from "react-router-dom";
import { Check } from "lucide-react";
import type { AppNotification } from "@/types/notifications";
import { notificationLinks } from "@/lib/notificationLinks";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { notificationMeta } from "./notificationMeta";
import { useMarkRead } from "./useNotificationMutations";

interface NotificationItemProps {
  notification: AppNotification;
}

export function NotificationItem({ notification }: NotificationItemProps) {
  const navigate = useNavigate();
  const markRead = useMarkRead();
  const { icon: Icon, label } = notificationMeta(notification.type);

  // Full row = navigate + mark read (optimistic feel: it disappears once the
  // list refetch lands after mark-read invalidation).
  const handleOpen = () => {
    markRead.mutate(notification.id);
    navigate(notificationLinks(notification));
  };

  const handleMarkRead = (e: React.MouseEvent) => {
    e.stopPropagation();
    markRead.mutate(notification.id);
  };

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={handleOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleOpen();
        }
      }}
      aria-label={`${label}: ${notification.title}`}
      className={cn(
        "flex cursor-pointer items-start gap-3 border-l-4 border-l-primary p-4",
        "transition-colors hover:bg-accent focus-visible:outline-none",
        "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      )}
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      >
        <Icon className="h-5 w-5" />
      </span>

      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="font-semibold leading-snug">{notification.title}</p>
        {notification.body ? (
          <p className="text-sm text-muted-foreground">{notification.body}</p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {fmtDate(notification.created_at)}
        </p>
      </div>

      <Button
        variant="ghost"
        size="icon"
        className="shrink-0"
        onClick={handleMarkRead}
        disabled={markRead.isPending}
        aria-label="Mark as read"
        title="Mark as read"
      >
        <Check className="h-4 w-4" />
      </Button>
    </Card>
  );
}
