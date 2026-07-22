import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";
import { financeApi } from "@/lib/api/endpoints";

// Owned by the Finance module. Signature, queryKey, and `enabled` gate are a
// frozen contract — only the queryFn is swapped from the foundation stub.
export function useHouseDues(houseId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.finance.dues(houseId as number),
    queryFn: () => financeApi.houseDues(houseId as number),
    enabled: typeof houseId === "number",
  });
}
