import {
  useCallback,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { tokenStore } from "@/lib/auth/tokenStore";
import { ApiError } from "@/types/common";
import type { LoginResponse, Me, Portal } from "@/types/auth";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./authContext";

function isMustChange(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    err.status === 403 &&
    (err.details as { password_state?: string })?.password_state ===
      "must_change"
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  // Portal chosen previously (persisted). Drives the ["me", portal] query key.
  const [portal, setPortalState] = useState<Portal | null>(
    () => tokenStore.getPortal() as Portal | null,
  );
  // Set to true after a successful login when password_state === "must_change".
  const [mustChange, setMustChange] = useState(false);

  const hasSession = !!tokenStore.getRefresh();

  const meQuery = useQuery<Me, ApiError>({
    queryKey: queryKeys.me(portal),
    queryFn: () => authApi.me(portal),
    enabled: hasSession && !mustChange,
    retry: false,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  const me = meQuery.data ?? null;

  const status: AuthStatus = useMemo(() => {
    if (mustChange) return "must_change";
    if (!hasSession) return "anon";
    if (meQuery.isError) {
      return isMustChange(meQuery.error) ? "must_change" : "anon";
    }
    if (meQuery.isPending || meQuery.isLoading) return "loading";
    if (me) return "authed";
    return "loading";
  }, [mustChange, hasSession, meQuery.isError, meQuery.error, meQuery.isPending, meQuery.isLoading, me]);

  const has = useCallback(
    (perm: string) => me?.permissions.includes(perm) ?? false,
    [me],
  );
  const hasModule = useCallback(
    (mod: string) => me?.modules.includes(mod) ?? false,
    [me],
  );

  const setPortal = useCallback(
    async (p: Portal) => {
      tokenStore.setPortal(p);
      // Fetch (and cache under the portal-keyed query) before flipping state, so
      // me.landing is populated by the time callers navigate. Updating portal
      // state then simply adopts this cached result — no duplicate request.
      await queryClient.fetchQuery({
        queryKey: queryKeys.me(p),
        queryFn: () => authApi.me(p),
      });
      setPortalState(p);
    },
    [queryClient],
  );

  const login = useCallback(
    async (email: string, password: string): Promise<LoginResponse> => {
      const res = await authApi.login(email, password);
      tokenStore.setTokens(res.access_token, res.refresh_token);

      if (res.password_state === "must_change") {
        setMustChange(true);
        return res;
      }

      setMustChange(false);
      if (res.available_portals.length === 1) {
        await setPortal(res.available_portals[0]);
      } else {
        // Multi-portal: leave portal null; the guard sends to /choose-portal.
        tokenStore.setPortal(null);
        setPortalState(null);
      }
      return res;
    },
    [setPortal],
  );

  const logout = useCallback(async () => {
    const rt = tokenStore.getRefresh();
    if (rt) {
      try {
        await authApi.logout(rt);
      } catch {
        // ignore network/logout errors
      }
    }
    tokenStore.clear();
    setPortalState(null);
    setMustChange(false);
    queryClient.clear();
    if (location.pathname !== "/login") location.assign("/login");
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      me,
      status,
      portal,
      availablePortals: me?.available_portals ?? [],
      has,
      hasModule,
      login,
      setPortal,
      logout,
    }),
    [me, status, portal, has, hasModule, login, setPortal, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
