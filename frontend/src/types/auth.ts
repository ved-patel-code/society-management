export type Portal = "admin" | "resident" | "platform";
export type PasswordState = "active" | "must_change";

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string; // "bearer"
  password_state: PasswordState;
  available_portals: Portal[];
}

export interface Me {
  user: {
    id: number;
    email: string;
    full_name: string | null;
    phone: string | null;
  };
  active_society_id: number | null;
  available_portals: Portal[];
  active_portal: Portal | null;
  modules: string[]; // nav tabs: e.g. ["notices","complaints","finance","notifications"]
  landing: string | null; // e.g. "notices"
  permissions: string[]; // dot-keys: e.g. ["notices.read","complaints.create",...]
  onboarding_required: boolean;
}
