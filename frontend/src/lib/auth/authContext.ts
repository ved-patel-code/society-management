import { createContext } from "react";
import type { LoginResponse, Me, Portal } from "@/types/auth";

export type AuthStatus = "loading" | "authed" | "anon" | "must_change";

export interface AuthContextValue {
  me: Me | null;
  status: AuthStatus;
  portal: Portal | null;
  availablePortals: Portal[];
  has: (perm: string) => boolean;
  hasModule: (mod: string) => boolean;
  login: (email: string, password: string) => Promise<LoginResponse>;
  setPortal: (p: Portal) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
