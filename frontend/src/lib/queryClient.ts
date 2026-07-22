import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/types/common";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Don't retry auth/permission/validation failures — only transient ones.
        if (error instanceof ApiError) {
          if ([400, 401, 403, 404, 409, 413, 415, 422].includes(error.status)) {
            return false;
          }
        }
        return failureCount < 2;
      },
    },
    mutations: { retry: false },
  },
});
