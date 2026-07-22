import type { ReactNode } from "react";
import type { ComplaintListItem } from "@/types/complaints";
import type { Column } from "@/components/common/DataView";
import { DataView } from "@/components/common/DataView";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fmtDate } from "@/lib/format";

interface ComplaintTableProps {
  rows: ComplaintListItem[];
  onRowClick: (row: ComplaintListItem) => void;
  empty?: ReactNode;
}

const columns: Column<ComplaintListItem>[] = [
  {
    header: "Reference",
    mobileLabel: "Reference",
    cell: (r) => <span className="font-medium">{r.reference}</span>,
  },
  {
    header: "Title",
    mobileLabel: "Title",
    cell: (r) => r.title,
  },
  {
    header: "House",
    mobileLabel: "House",
    cell: (r) => r.house_display_code ?? "—",
  },
  {
    header: "Category",
    mobileLabel: "Category",
    cell: (r) => r.category_name,
  },
  {
    header: "Status",
    mobileLabel: "Status",
    cell: (r) => <StatusBadge status={r.status} />,
  },
  {
    header: "Updated",
    mobileLabel: "Updated",
    cell: (r) => fmtDate(r.updated_at),
  },
];

export function ComplaintTable({ rows, onRowClick, empty }: ComplaintTableProps) {
  return (
    <DataView
      columns={columns}
      rows={rows}
      keyField={(r) => r.id}
      onRowClick={onRowClick}
      empty={empty}
    />
  );
}
