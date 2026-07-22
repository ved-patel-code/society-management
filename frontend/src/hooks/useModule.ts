import { useAuth } from "@/hooks/useAuth";

export function useModule(mod: string): boolean {
  return useAuth().hasModule(mod);
}
