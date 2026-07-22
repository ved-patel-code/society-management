import type { HouseDue } from "@/types/finance";
import type { Column } from "@/components/common/DataView";
import { DataView } from "@/components/common/DataView";
import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { formatCurrency, fmtDateOnly } from "@/lib/format";

interface DuesHistoryProps {
  history: HouseDue[];
}

export function DuesHistory({ history }: DuesHistoryProps) {
  // Backend returns oldest-first; show newest-first for a familiar statement view.
  // (Read-only: no row actions, no click handler.)
  const rows = [...history].reverse();

  const columns: Column<HouseDue>[] = [
    {
      header: "Period",
      cell: (d) => `${d.period_month}/${d.period_year}`,
    },
    {
      header: "Amount",
      cell: (d) => formatCurrency(d.amount_due),
    },
    {
      header: "Status",
      cell: (d) => (
        <StatusBadge status={d.is_overdue ? "overdue" : d.status} />
      ),
    },
    {
      header: "Paid on",
      cell: (d) => fmtDateOnly(d.paid_at),
    },
  ];

  return (
    <DataView
      columns={columns}
      rows={rows}
      keyField={(d) => d.id}
      empty={
        <EmptyState
          title="No dues on record"
          description="Maintenance dues will appear here once they are generated for your house."
        />
      }
    />
  );
}
