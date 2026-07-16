import { Lock } from "lucide-react";
import { EmptyState } from "@/components/common/EmptyState";

export function Forbidden() {
  return (
    <EmptyState
      icon={<Lock className="h-8 w-8" />}
      title="You don't have access to this."
      description="If you think this is a mistake, contact your society administrator."
    />
  );
}
