import { Home } from "lucide-react";
import { SectionCard } from "@/components/common/SectionCard";

interface HouseHeaderProps {
  // Derived (never fetched from /houses): the resident's own house code.
  houseDisplayCode: string | null | undefined;
}

export function HouseHeader({ houseDisplayCode }: HouseHeaderProps) {
  return (
    <SectionCard>
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-md bg-accent text-accent-foreground">
          <Home className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="space-y-0.5">
          <p className="text-sm text-muted-foreground">Your house</p>
          <p className="text-lg font-semibold tracking-tight">
            {houseDisplayCode || "Your house"}
          </p>
        </div>
      </div>
    </SectionCard>
  );
}
