import type { ComplaintListItem } from "@/types/complaints";
import type { HouseDuesResponse } from "@/types/finance";
import { ApiError } from "@/types/common";
import { useComplaints } from "@/hooks/useComplaints";
import { useHouseDues } from "@/hooks/useHouseDues";
import { PageHeader } from "@/components/common/PageHeader";
import { SectionCard } from "@/components/common/SectionCard";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Forbidden } from "@/components/common/Forbidden";
import { getErrorMessage } from "@/lib/format";
import { HouseHeader } from "@/components/finance/HouseHeader";
import { DuesSummary } from "@/components/finance/DuesSummary";
import { DuesHistory } from "@/components/finance/DuesHistory";
import { HowToPayNote } from "@/components/finance/HowToPayNote";
import { HouseComplaints } from "@/components/finance/HouseComplaints";

// Empty HouseDuesResponse used when the dues endpoint 404s (house not found) —
// rendered as an empty state, never a crash.
const EMPTY_DUES: HouseDuesResponse = {
  house_id: 0,
  outstanding: [],
  outstanding_total: "0",
  history: [],
};

export function FinancePage() {
  // Derive the resident's house from their own complaints. Residents have NO
  // /houses/* access — the house_id / house_display_code is only ever read from
  // data the resident already owns (here, a complaint payload). Never fetched.
  const houseProbe = useComplaints({ page: 1, page_size: 1 });
  const probeItems = (houseProbe.data?.items ?? []) as ComplaintListItem[];
  const derivedHouse = probeItems[0];
  const houseId = derivedHouse?.house_id;
  const houseCode = derivedHouse?.house_display_code;

  const duesQuery = useHouseDues(houseId);

  return (
    <div className="space-y-6">
      <PageHeader title="Financial" />

      {houseProbe.isLoading ? (
        <LoadingState />
      ) : typeof houseId !== "number" ? (
        <EmptyState
          title="No house data yet"
          description="Once your maintenance dues or complaints are on record, your house details appear here."
        />
      ) : (
        <ResidentFinance
          houseId={houseId}
          houseCode={houseCode}
          duesQuery={duesQuery}
        />
      )}
    </div>
  );
}

interface ResidentFinanceProps {
  houseId: number;
  houseCode: string | null | undefined;
  duesQuery: ReturnType<typeof useHouseDues>;
}

function ResidentFinance({
  houseId,
  houseCode,
  duesQuery,
}: ResidentFinanceProps) {
  const { data, isLoading, isError, error } = duesQuery;

  // 403 -> resident holds finance.read but is not a current occupant of this
  // house: show <Forbidden/> for the dues sections (own-house only).
  const forbidden =
    isError &&
    error instanceof ApiError &&
    error.status === 403;

  // 404 -> "House not found in this society": treat as empty (no dues), no crash.
  const notFound =
    isError && error instanceof ApiError && error.status === 404;

  const dues: HouseDuesResponse | undefined = notFound
    ? EMPTY_DUES
    : (data as HouseDuesResponse | undefined);

  return (
    <div className="space-y-6">
      <HouseHeader houseDisplayCode={houseCode} />

      {forbidden ? (
        <Forbidden />
      ) : isLoading ? (
        <LoadingState />
      ) : isError && !notFound ? (
        <SectionCard title="Dues">
          <p className="text-sm text-muted-foreground">
            {getErrorMessage(error)}
          </p>
        </SectionCard>
      ) : dues ? (
        <>
          <DuesSummary dues={dues} />
          <SectionCard title="Dues history">
            <DuesHistory history={dues.history} />
          </SectionCard>
        </>
      ) : null}

      <HowToPayNote />

      <HouseComplaints houseId={houseId} />
    </div>
  );
}
