import { CheckCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMarkAllRead } from "./useNotificationMutations";

interface MarkAllReadButtonProps {
  unreadCount: number;
}

export function MarkAllReadButton({ unreadCount }: MarkAllReadButtonProps) {
  const markAllRead = useMarkAllRead();
  const disabled = unreadCount === 0 || markAllRead.isPending;

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => markAllRead.mutate()}
      disabled={disabled}
    >
      <CheckCheck className="h-4 w-4" />
      Mark all read
    </Button>
  );
}
