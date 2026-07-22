import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";
import { complaintsApi } from "@/lib/api/endpoints";
import { useAuth } from "@/hooks/useAuth";

export interface ComplaintListParams {
  page?: number;
  page_size?: number;
  status?: string;
  category_id?: number;
  house_id?: number;
  date_from?: string;
  date_to?: string;
  q?: string;
}

// Owned by the Complaints module. Finance reuses this hook to list a house's
// complaints, so the signature + queryKey + `enabled` gate are a frozen contract.
export function useComplaints(params: ComplaintListParams) {
  const { hasModule } = useAuth();
  return useQuery({
    queryKey: queryKeys.complaints.list(params as Record<string, unknown>),
    queryFn: () => complaintsApi.list(params),
    enabled: hasModule("complaints"),
  });
}

export function useComplaintCategories() {
  return useQuery({
    queryKey: queryKeys.complaints.categories(),
    queryFn: complaintsApi.categories,
  });
}
