import type { ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";

interface IfModuleProps {
  module: string;
  children: ReactNode;
  fallback?: ReactNode;
}

export function IfModule({ module, children, fallback = null }: IfModuleProps) {
  const { hasModule } = useAuth();
  return <>{hasModule(module) ? children : fallback}</>;
}
