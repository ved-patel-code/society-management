import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BellOff, ChevronLeft, ChevronRight } from "lucide-react";
import { ApiError } from "@/types/common";
import { notificationsApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { getErrorMessage } from "@/lib/format";
import { IfModule } from "@/components/common/IfModule";
import { Forbidden } from "@/components/common/Forbidden";
import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Button } from "@/components/ui/button";
import { NotificationItem } from "@/components/notifications/NotificationItem";
import { MarkAllReadButton } from "@/components/notifications/MarkAllReadButton";

const PAGE_SIZE = 20;

function NotificationsContent() {
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: queryKeys.notifications.list(page),
    queryFn: () => notificationsApi.list(page, PAGE_SIZE),
  });

  const unreadCount = data?.unread_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(unreadCount / PAGE_SIZE));

  // The feed is unread-only: clearing items can shrink the total below the
  // current page. Snap back so we never sit on a now-empty trailing page.
  useEffect(() => {
    if (!isLoading && page > totalPages) setPage(totalPages);
  }, [isLoading, page, totalPages]);

  const header = (
    <PageHeader
      title="Notifications"
      description={
        unreadCount > 0
          ? `${unreadCount} unread`
          : "Your unread notifications appear here."
      }
      actions={<MarkAllReadButton unreadCount={unreadCount} />}
    />
  );

  if (isError) {
    if (error instanceof ApiError && error.status === 403) {
      return (
        <div className="space-y-6">
          {header}
          <Forbidden />
        </div>
      );
    }
    return (
      <div className="space-y-6">
        {header}
        <EmptyState
          title="Couldn't load notifications"
          description={getErrorMessage(error)}
        />
      </div>
    );
  }

  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      {header}

      {isLoading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<BellOff className="h-8 w-8" />}
          title="You're all caught up"
          description="No unread notifications right now."
        />
      ) : (
        <>
          <ul className="space-y-3">
            {items.map((n) => (
              <li key={n.id}>
                <NotificationItem notification={n} />
              </li>
            ))}
          </ul>

          {totalPages > 1 ? (
            <div className="flex items-center justify-between pt-2">
              <p className="text-sm text-muted-foreground">
                Page {page} of {totalPages}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

export function NotificationsPage() {
  return (
    <IfModule module="notifications" fallback={<Forbidden />}>
      <NotificationsContent />
    </IfModule>
  );
}
