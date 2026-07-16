import { Badge } from "@/components/ui/badge";

type BadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "warning"
  | "info"
  | "muted";

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  // success (green)
  owned: "success",
  resolved: "success",
  paid: "success",
  published: "success",
  recorded: "success",
  // destructive (red)
  open: "destructive",
  outstanding: "destructive",
  overdue: "destructive",
  // warning (amber)
  in_progress: "warning",
  to_let: "warning",
  for_sale: "warning",
  // info (blue)
  rented: "info",
  // muted (gray)
  empty: "muted",
  closed: "muted",
  withdrawn: "muted",
  archived: "muted",
  draft: "muted",
  voided: "muted",
};

function toLabel(status: string): string {
  return status
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const variant = STATUS_VARIANT[status] ?? "muted";
  return <Badge variant={variant}>{toLabel(status)}</Badge>;
}
