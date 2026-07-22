import { ApiError, type ApiErrorShape } from "@/types/common";
import { tokenStore } from "@/lib/auth/tokenStore";

const API_BASE = import.meta.env.VITE_API_BASE as string;

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown; // JSON-serialized unless it's FormData
  public?: boolean; // skip auth header + skip refresh (login/refresh/forgot)
  _retried?: boolean; // internal
}

// ---- single-flight refresh (the core rotation-safety mechanism) ----
let refreshPromise: Promise<void> | null = null;

async function refresh(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const rt = tokenStore.getRefresh();
      if (!rt) {
        throw new ApiError(401, {
          code: "authentication_error",
          message: "No session",
          details: {},
        });
      }
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) {
        const errBody = (await res.json().catch(() => ({}))) as ApiErrorShape;
        throw new ApiError(res.status, errBody);
      }
      const data = (await res.json()) as {
        access_token: string;
        refresh_token: string;
        token_type: string;
      };
      // persist BEFORE any retry
      tokenStore.setTokens(data.access_token, data.refresh_token);
    })();
    // clear the latch when done (success or fail) so future 401s can refresh again
    refreshPromise.finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export function hardLogout() {
  tokenStore.clear();
  if (location.pathname !== "/login") location.assign("/login");
}

function isFormData(v: unknown): v is FormData {
  return typeof FormData !== "undefined" && v instanceof FormData;
}

export async function apiFetch<T = unknown>(
  path: string,
  opts: ApiFetchOptions = {},
): Promise<T> {
  const { body, public: isPublic, _retried, headers, ...rest } = opts;

  const finalHeaders = new Headers(headers as HeadersInit | undefined);

  // Auth header unless this is a public call.
  if (!isPublic) {
    const access = tokenStore.getAccess();
    if (access) finalHeaders.set("Authorization", `Bearer ${access}`);
  }

  // Body handling: FormData -> let browser set the Content-Type boundary.
  let finalBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (isFormData(body)) {
      finalBody = body;
    } else {
      if (!finalHeaders.has("Content-Type")) {
        finalHeaders.set("Content-Type", "application/json");
      }
      finalBody = JSON.stringify(body);
    }
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: finalHeaders,
    body: finalBody,
  });

  if (res.status === 204) return undefined as T;

  if (res.ok) {
    // Guard empty body (e.g. 200 with no content).
    const text = await res.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  }

  // Non-ok: parse the {code,message,details} envelope into ApiError.
  const errBody = (await res.json().catch(() => ({}))) as ApiErrorShape;
  const apiError = new ApiError(res.status, errBody);

  // 401: refresh + replay once (never on public calls or the refresh call itself).
  if (res.status === 401 && !isPublic && !_retried) {
    try {
      await refresh();
    } catch {
      hardLogout();
      throw apiError;
    }
    return apiFetch<T>(path, { ...opts, _retried: true });
  }

  // 403 forced password change -> route to change-password.
  if (
    res.status === 403 &&
    (apiError.details as { password_state?: string })?.password_state ===
      "must_change"
  ) {
    if (location.pathname !== "/change-password") {
      location.assign("/change-password");
    }
    throw apiError;
  }

  throw apiError;
}
