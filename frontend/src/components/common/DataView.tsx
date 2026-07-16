import type { ReactNode } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/useIsMobile";

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  mobileLabel?: string;
  className?: string;
}

export interface DataViewProps<T> {
  columns: Column<T>[];
  rows: T[];
  keyField: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
}

export function DataView<T>({
  columns,
  rows,
  keyField,
  onRowClick,
  empty,
}: DataViewProps<T>) {
  const isMobile = useIsMobile();

  if (rows.length === 0) {
    return <>{empty ?? null}</>;
  }

  if (isMobile) {
    return (
      <div className="space-y-3">
        {rows.map((row) => (
          <Card
            key={keyField(row)}
            className={cn("p-4", onRowClick && "cursor-pointer hover:bg-accent/50")}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
          >
            <dl className="space-y-2">
              {columns.map((col, i) => (
                <div
                  key={i}
                  className="flex items-start justify-between gap-3 text-sm"
                >
                  <dt className="font-medium text-muted-foreground">
                    {col.mobileLabel ?? col.header}
                  </dt>
                  <dd className={cn("text-right", col.className)}>
                    {col.cell(row)}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((col, i) => (
              <TableHead key={i} className={col.className}>
                {col.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={keyField(row)}
              className={onRowClick ? "cursor-pointer" : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col, i) => (
                <TableCell key={i} className={col.className}>
                  {col.cell(row)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
