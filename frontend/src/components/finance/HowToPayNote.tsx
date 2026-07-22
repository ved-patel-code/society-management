import { Info } from "lucide-react";
import { SectionCard } from "@/components/common/SectionCard";

// Static informational note. There is NO online-payment endpoint on this page —
// dues are strictly read-only. Do NOT add a "Pay now" control here.
export function HowToPayNote() {
  return (
    <SectionCard title="How to pay">
      <div className="flex gap-3 text-sm text-muted-foreground">
        <Info
          className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <p>
          Maintenance dues are collected offline. Please pay your society
          administrator or committee and they will record your payment, after
          which it reflects here. For any queries, contact your society office.
        </p>
      </div>
    </SectionCard>
  );
}
