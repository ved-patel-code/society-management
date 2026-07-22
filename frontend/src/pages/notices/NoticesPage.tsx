import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, ChevronLeft, ChevronRight, Megaphone } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/types/common";
import { noticesApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { getErrorMessage } from "@/lib/format";
import { useModule } from "@/hooks/useModule";
import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Forbidden } from "@/components/common/Forbidden";
import { Button } from "@/components/ui/button";
import { NoticeListItem } from "@/components/notices/NoticeListItem";

const PAGE_SIZE = 20;

export function NoticesPage() {
  const hasNotices = useModule("notices");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);

  const listQuery = useQuery({
    queryKey: queryKeys.notices.list(page),
    queryFn: () => noticesApi.list(page, PAGE_SIZE),
    enabled: hasNotices,
  });

  const readAll = useMutation({
    mutationFn: () => noticesApi.readAll(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notices", "list"] });
      queryClient.invalidateQueries({
        queryKey: queryKeys.notifications.unread(),
      });
      toast.success("All notices marked read");
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  if (!hasNotices) return <Forbidden />;

  const unreadCount = listQuery.data?.unread_count ?? 0;
  const total = listQuery.data?.total ?? 0;
  const items = listQuery.data?.items ?? [];
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const header = (
    <PageHeader
      title="Notice Board"
      description={unreadCount > 0 ? `${unreadCount} unread` : "All caught up"}
      actions={
        <Button
          variant="outline"
          size="sm"
          disabled={unreadCount === 0 || readAll.isPending}
          onClick={() => readAll.mutate()}
        >
          <CheckCheck className="h-4 w-4" />
          Mark all read
        </Button>
      }
    />
  );

  let content;
  if (listQuery.isLoading) {
    content = <LoadingState rows={6} />;
  } else if (listQuery.isError) {
    const err = listQuery.error;
    if (err instanceof ApiError && err.status === 403) {
      return (
        <div className="space-y-6">
          {header}
          <Forbidden />
        </div>
      );
    }
    content = (
      <EmptyState
        icon={<Megaphone className="h-8 w-8" />}
        title="Couldn't load notices"
        description={getErrorMessage(err)}
        action={
          <Button variant="outline" onClick={() => listQuery.refetch()}>
            Try again
          </Button>
        }
      />
    );
  } else if (items.length === 0) {
    content = (
      <EmptyState
        icon={<Megaphone className="h-8 w-8" />}
        title="No notices yet"
        description="Society notices will appear here when they're published."
      />
    );
  } else {
    content = (
      <div className="space-y-3">
        {items.map((notice) => (
          <NoticeListItem
            key={notice.id}
            notice={notice}
            onOpen={(id) => navigate(`/notices/${id}`)}
          />
        ))}
        {totalPages > 1 ? (
          <div className="flex items-center justify-between pt-2">
            <span className="text-sm text-muted-foreground">
              Page {page} of {totalPages}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1 || listQuery.isFetching}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft className="h-4 w-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages || listQuery.isFetching}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {header}
      {content}
    </div>
  );
}
