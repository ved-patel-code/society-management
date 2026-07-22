import { Paperclip, Pin } from "lucide-react";
import type { NoticeListItem as NoticeListItemDto } from "@/types/notices";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { fmtDate } from "@/lib/format";

interface NoticeListItemProps {
  notice: NoticeListItemDto;
  onOpen: (id: number) => void;
}

export function NoticeListItem({ notice, onOpen }: NoticeListItemProps) {
  const unread = !notice.is_read;

  return (
    <Card
      role="button"
      tabIndex={0}
      aria-label={`Open notice: ${notice.title}${unread ? " (unread)" : ""}`}
      onClick={() => onOpen(notice.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(notice.id);
        }
      }}
      className={cn(
        "cursor-pointer p-4 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        notice.is_pinned && "border-primary/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            {notice.is_pinned ? (
              <Pin
                className="h-4 w-4 shrink-0 text-primary"
                aria-label="Pinned"
              />
            ) : null}
            <h3
              className={cn(
                "truncate text-base",
                unread ? "font-semibold" : "font-medium",
              )}
            >
              {notice.title}
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{fmtDate(notice.published_at)}</span>
            {notice.attachment_count > 0 ? (
              <span className="inline-flex items-center gap-1">
                <Paperclip className="h-3 w-3" aria-hidden="true" />
                {notice.attachment_count}
                <span className="sr-only">
                  {notice.attachment_count === 1 ? "attachment" : "attachments"}
                </span>
              </span>
            ) : null}
          </div>
        </div>
        {unread ? (
          <Badge variant="info" className="shrink-0">
            Unread
          </Badge>
        ) : null}
      </div>
    </Card>
  );
}
