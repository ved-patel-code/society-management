import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import type { ComplaintListItem } from "@/types/complaints";
import { useComplaints } from "@/hooks/useComplaints";
import { SectionCard } from "@/components/common/SectionCard";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { getErrorMessage } from "@/lib/format";

interface HouseComplaintsProps {
  houseId: number;
}

export function HouseComplaints({ houseId }: HouseComplaintsProps) {
  const { data, isLoading, isError, error } = useComplaints({
    house_id: houseId,
    page: 1,
    page_size: 5,
  });

  // The frozen useComplaints stub returns `unknown[]`; treat items defensively.
  const items = (data?.items ?? []) as ComplaintListItem[];

  const viewAll = (
    <Link
      to="/complaints"
      className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
    >
      View all <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
    </Link>
  );

  let body;
  if (isLoading) {
    body = <LoadingState rows={3} />;
  } else if (isError) {
    body = (
      <p className="text-sm text-muted-foreground">{getErrorMessage(error)}</p>
    );
  } else if (items.length === 0) {
    body = (
      <EmptyState
        title="No complaints yet"
        description="Complaints raised for your house will appear here."
      />
    );
  } else {
    body = (
      <ul className="divide-y">
        {items.map((c) => (
          <li key={c.id}>
            <Link
              to={`/complaints/${c.id}`}
              className="flex items-center justify-between gap-3 py-3 hover:bg-accent/40"
            >
              <div className="min-w-0 space-y-0.5">
                <p className="truncate text-sm font-medium">
                  {c.title ?? "Complaint"}
                </p>
                {c.reference ? (
                  <p className="truncate text-xs text-muted-foreground">
                    {c.reference}
                  </p>
                ) : null}
              </div>
              {c.status ? <StatusBadge status={c.status} /> : null}
            </Link>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <SectionCard
      title="Recent complaints"
      description="For your house"
      actions={viewAll}
    >
      {body}
    </SectionCard>
  );
}
