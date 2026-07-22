import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";
import { notificationsApi } from "@/lib/api/endpoints";
import { useAuth } from "@/hooks/useAuth";

// Returns the unread count for the bell + sidebar badge.
// FROZEN signature + query key. The Notifications session may extend, not change these.
export function useUnreadNotifications(): { count: number; isLoading: boolean } {
  const { hasModule } = useAuth();
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.notifications.unread(),
    queryFn: () => notificationsApi.unreadCount(),
    refetchInterval: 30000,
    enabled: hasModule("notifications"),
  });
  return { count: data?.unread_count ?? 0, isLoading };
}
