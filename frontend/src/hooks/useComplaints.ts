import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/queryKeys";
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

// STUB — Complaints session replaces the queryFn with complaintsApi.list(params).
// Keep this signature + queryKey + the `enabled` gate identical.
export function useComplaints(params: ComplaintListParams) {
  const { hasModule } = useAuth();
  return useQuery({
    queryKey: queryKeys.complaints.list(params as Record<string, unknown>),
    queryFn: async () => ({ items: [] as unknown[], total: 0 }), // stub: empty until Complaints lands
    enabled: hasModule("complaints"),
  });
}

// STUB — Complaints session replaces the queryFn with complaintsApi.categories.
export function useComplaintCategories() {
  return useQuery({
    queryKey: queryKeys.complaints.categories(),
    queryFn: async () => [] as unknown[], // stub
  });
}
