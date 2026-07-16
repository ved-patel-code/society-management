// Single-writer token store.
// Access token: in-memory + sessionStorage mirror (survives reload). Refresh: localStorage.
const ACCESS_KEY = "sm.access"; // sessionStorage
const REFRESH_KEY = "sm.refresh"; // localStorage
const PORTAL_KEY = "sm.portal"; // localStorage (chosen portal)

let accessToken: string | null = sessionStorage.getItem(ACCESS_KEY);

export const tokenStore = {
  getAccess: () => accessToken,
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  getPortal: () => localStorage.getItem(PORTAL_KEY),
  setPortal: (p: string | null) =>
    p ? localStorage.setItem(PORTAL_KEY, p) : localStorage.removeItem(PORTAL_KEY),
  // Always overwrite with the NEWEST tokens. Never keep an old refresh token.
  setTokens: (access: string, refresh?: string) => {
    accessToken = access;
    sessionStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    accessToken = null;
    sessionStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(PORTAL_KEY);
  },
};

// Cross-tab: keep in-memory access token in sync if another tab refreshes.
window.addEventListener("storage", (e) => {
  if (e.key === ACCESS_KEY) accessToken = e.newValue;
});
