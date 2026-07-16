import type { ReactNode } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { LoadingState } from "@/components/common/LoadingState";
import { EmptyState } from "@/components/common/EmptyState";

export function RequireAuth({ children }: { children?: ReactNode }) {
  const { status, me, availablePortals } = useAuth();

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="w-full max-w-md">
          <LoadingState rows={4} />
        </div>
      </div>
    );
  }

  if (status === "anon") return <Navigate to="/login" replace />;
  if (status === "must_change") return <Navigate to="/change-password" replace />;

  // authed from here.
  if (me && me.active_portal === null && availablePortals.length > 1) {
    return <Navigate to="/choose-portal" replace />;
  }

  if (me && me.onboarding_required) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <EmptyState
          title="Onboarding pending"
          description="Your society is still being set up. Please check back later."
        />
      </div>
    );
  }

  return <>{children ?? <Outlet />}</>;
}
