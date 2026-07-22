import type { StatusHistory } from "@/types/complaints";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fmtDate } from "@/lib/format";
import { ArrowRight } from "lucide-react";

interface ComplaintTimelineProps {
  entries: StatusHistory[];
}

export function ComplaintTimeline({ entries }: ComplaintTimelineProps) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No status history yet.</p>
    );
  }

  return (
    <ol className="space-y-4">
      {entries.map((e) => (
        <li key={e.id} className="flex gap-3">
          <div
            className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary"
            aria-hidden="true"
          />
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              {e.from_status ? (
                <>
                  <StatusBadge status={e.from_status} />
                  <ArrowRight
                    className="h-3.5 w-3.5 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <StatusBadge status={e.to_status} />
                </>
              ) : (
                <StatusBadge status={e.to_status} />
              )}
            </div>
            {e.note ? <p className="text-sm">{e.note}</p> : null}
            <p className="text-xs text-muted-foreground">
              {fmtDate(e.created_at)}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
