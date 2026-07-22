import { Skeleton } from "@/components/ui/skeleton";

interface LoadingStateProps {
  rows?: number;
}

export function LoadingState({ rows = 5 }: LoadingStateProps) {
  return (
    <div className="w-full space-y-3 p-4" role="status" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}
