import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";

// STUB — Finance session replaces the queryFn with financeApi.houseDues(houseId).
export function useHouseDues(houseId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.finance.dues(houseId as number),
    queryFn: async () => null as unknown, // stub
    enabled: typeof houseId === "number",
  });
}
