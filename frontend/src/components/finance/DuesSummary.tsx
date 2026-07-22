import type { HouseDuesResponse } from "@/types/finance";
import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency, fmtDateOnly } from "@/lib/format";
import { cn } from "@/lib/utils";

interface DuesSummaryProps {
  dues: HouseDuesResponse;
}

interface StatProps {
  label: string;
  value: string;
  accent?: boolean;
}

function Stat({ label, value, accent }: StatProps) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p
          className={cn(
            "mt-1 text-2xl font-semibold tracking-tight",
            accent && "text-destructive",
          )}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

export function DuesSummary({ dues }: DuesSummaryProps) {
  const hasOverdue = dues.outstanding.some((d) => d.is_overdue);
  const nextDue = dues.outstanding[0]?.due_date; // outstanding is oldest-first

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <Stat
        label="Outstanding total"
        value={formatCurrency(dues.outstanding_total)}
        accent={hasOverdue || dues.outstanding.length > 0}
      />
      <Stat label="Months pending" value={String(dues.outstanding.length)} />
      <Stat
        label="Next due date"
        value={
          dues.outstanding.length === 0 ? "All paid" : fmtDateOnly(nextDue)
        }
        accent={hasOverdue}
      />
    </div>
  );
}
