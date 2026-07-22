import { useEffect, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, Eye, Paperclip, Pin } from "lucide-react";
import type { NoticeAttachment } from "@/types/notices";
import { ApiError } from "@/types/common";
import { noticesApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { fmtDate, getErrorMessage } from "@/lib/format";
import { useModule } from "@/hooks/useModule";
import { PageHeader } from "@/components/common/PageHeader";
import { SectionCard } from "@/components/common/SectionCard";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Forbidden } from "@/components/common/Forbidden";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { NoticeBody } from "@/components/notices/NoticeBody";

function BackLink() {
  return (
    <Button variant="ghost" size="sm" asChild className="-ml-2 w-fit">
      <Link to="/notices">
        <ArrowLeft className="h-4 w-4" />
        Back to Notice Board
      </Link>
    </Button>
  );
}

function AttachmentRow({ attachment }: { attachment: NoticeAttachment }) {
  const hasDownload = Boolean(attachment.download_url);
  const label = `Attachment #${attachment.vault_document_id}`;
  return (
    <li className="flex items-center justify-between gap-3 rounded-md border p-3">
      <span className="flex min-w-0 items-center gap-2 text-sm">
        <Paperclip className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="truncate">{label}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        {attachment.preview_url ? (
          <Button variant="outline" size="sm" asChild>
            <a href={attachment.preview_url} target="_blank" rel="noopener noreferrer">
              <Eye className="h-4 w-4" />
              Preview
            </a>
          </Button>
        ) : null}
        {hasDownload ? (
          <Button variant="outline" size="sm" asChild>
            <a
              href={attachment.download_url as string}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Download className="h-4 w-4" />
              Download
            </a>
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled title="Link unavailable">
            <Download className="h-4 w-4" />
            Unavailable
          </Button>
        )}
      </span>
    </li>
  );
}

export function NoticeDetailPage() {
  const hasNotices = useModule("notices");
  const params = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const id = Number(params.id);
  const validId = Number.isInteger(id) && id > 0;

  const detailQuery = useQuery({
    queryKey: queryKeys.notices.detail(id),
    queryFn: () => noticesApi.detail(id),
    enabled: hasNotices && validId,
    retry: false,
  });

  // Opening the detail marks it read server-side. When that first successful load
  // lands, refresh the feed's unread dots and the bell badge — exactly once per open.
  const invalidatedFor = useRef<number | null>(null);
  useEffect(() => {
    if (detailQuery.isSuccess && invalidatedFor.current !== id) {
      invalidatedFor.current = id;
      queryClient.invalidateQueries({ queryKey: ["notices", "list"] });
      queryClient.invalidateQueries({
        queryKey: queryKeys.notifications.unread(),
      });
    }
  }, [detailQuery.isSuccess, id, queryClient]);

  if (!hasNotices) return <Forbidden />;

  if (!validId) {
    return (
      <div className="space-y-6">
        <BackLink />
        <EmptyState
          title="Notice not found"
          description="This notice may have been withdrawn or never existed."
        />
      </div>
    );
  }

  if (detailQuery.isLoading) {
    return (
      <div className="space-y-6">
        <BackLink />
        <LoadingState rows={6} />
      </div>
    );
  }

  if (detailQuery.isError) {
    const err = detailQuery.error;
    if (err instanceof ApiError && err.status === 403) {
      return (
        <div className="space-y-6">
          <BackLink />
          <Forbidden />
        </div>
      );
    }
    if (err instanceof ApiError && err.status === 404) {
      return (
        <div className="space-y-6">
          <BackLink />
          <EmptyState
            title="Notice not found"
            description="This notice may have been withdrawn or never existed."
          />
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <BackLink />
        <EmptyState
          title="Couldn't load this notice"
          description={getErrorMessage(err)}
          action={
            <Button variant="outline" onClick={() => detailQuery.refetch()}>
              Try again
            </Button>
          }
        />
      </div>
    );
  }

  const notice = detailQuery.data;
  if (!notice) {
    return (
      <div className="space-y-6">
        <BackLink />
        <LoadingState rows={6} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />
      <PageHeader
        title={notice.title}
        actions={<StatusBadge status={notice.status} />}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        {notice.is_pinned ? (
          <span className="inline-flex items-center gap-1 text-primary">
            <Pin className="h-4 w-4" aria-hidden="true" />
            Pinned
          </span>
        ) : null}
        <span>Published {fmtDate(notice.published_at)}</span>
        {notice.expires_at ? <span>Expires {fmtDate(notice.expires_at)}</span> : null}
      </div>

      <SectionCard>
        <NoticeBody html={notice.body} />
      </SectionCard>

      {notice.attachments.length > 0 ? (
        <SectionCard title="Attachments">
          <ul className="space-y-2">
            {notice.attachments.map((a) => (
              <AttachmentRow key={a.id} attachment={a} />
            ))}
          </ul>
        </SectionCard>
      ) : null}
    </div>
  );
}
