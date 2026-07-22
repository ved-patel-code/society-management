import { useAuth } from "@/hooks/useAuth";

export function useCan(perm: string): boolean {
  return useAuth().has(perm);
}
