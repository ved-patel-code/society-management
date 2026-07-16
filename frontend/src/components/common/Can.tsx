import type { ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";

interface CanProps {
  permission: string;
  children: ReactNode;
  fallback?: ReactNode;
}

export function Can({ permission, children, fallback = null }: CanProps) {
  const { has } = useAuth();
  return <>{has(permission) ? children : fallback}</>;
}
