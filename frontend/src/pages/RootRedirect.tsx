import { Navigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";

export function RootRedirect() {
  const { me } = useAuth();
  const landing = me?.landing ? `/${me.landing}` : "/notices";
  return <Navigate to={landing} replace />;
}
