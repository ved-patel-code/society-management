import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { notificationsApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { getErrorMessage } from "@/lib/format";

// Invalidate BOTH the unread badge (shell bell + sidebar) and every list page.
// list() is keyed by page, so invalidate the shared prefix ["notifications","list"].
function useInvalidateNotifications() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: queryKeys.notifications.unread() });
    qc.invalidateQueries({ queryKey: ["notifications", "list"] });
  };
}

export function useMarkRead() {
  const invalidate = useInvalidateNotifications();
  return useMutation({
    mutationFn: (id: number) => notificationsApi.markRead(id),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e)),
  });
}

export function useMarkAllRead() {
  const invalidate = useInvalidateNotifications();
  return useMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: (res) => {
      invalidate();
      toast.success(
        res.cleared > 0 ? "All caught up" : "Nothing to mark read",
      );
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });
}
