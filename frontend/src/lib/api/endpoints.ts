import { apiFetch } from "./client";
import type { LoginResponse, Me, Portal } from "@/types/auth";
import type {
  NoticeDetail,
  NoticeListResponse,
} from "@/types/notices";
import type {
  Category,
  ComplaintCreateBody,
  ComplaintDetail,
  ComplaintImage,
  ComplaintListItem,
  ComplaintUpdateBody,
} from "@/types/complaints";
import type { HouseDuesResponse } from "@/types/finance";
import type { NotificationsResponse } from "@/types/notifications";
import type { Paginated } from "@/types/common";

// --- AUTH (implemented fully in the foundation) ---
export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<LoginResponse>("/auth/login", {
      method: "POST",
      public: true,
      body: { email, password },
    }),
  logout: (refresh_token: string) =>
    apiFetch<{ message: string }>("/auth/logout", {
      method: "POST",
      public: true,
      body: { refresh_token },
    }),
  changePassword: (current_password: string, new_password: string) =>
    apiFetch<{ message: string }>("/auth/change-password", {
      method: "POST",
      body: { current_password, new_password },
    }),
  forgotPassword: (email: string) =>
    apiFetch<{ message: string }>("/auth/forgot-password", {
      method: "POST",
      public: true,
      body: { email },
    }),
  me: (portal: Portal | null) =>
    apiFetch<Me>(`/me${portal ? `?portal=${encodeURIComponent(portal)}` : ""}`),
};

// --- MODULE endpoints: correct resident paths; module sessions verify/refine ---

export const noticesApi = {
  list: (page = 1, pageSize = 20) =>
    apiFetch<NoticeListResponse>(`/notices?page=${page}&page_size=${pageSize}`),
  detail: (id: number) => apiFetch<NoticeDetail>(`/notices/${id}`),
  readAll: () => apiFetch<void>("/notices/read-all", { method: "POST" }),
};

export interface ComplaintListQuery {
  page?: number;
  page_size?: number;
  status?: string;
  category_id?: number;
  house_id?: number;
  date_from?: string;
  date_to?: string;
  q?: string;
}

function toQueryString(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const complaintsApi = {
  categories: () => apiFetch<Category[]>("/complaints/categories"),
  list: (params: ComplaintListQuery = {}) =>
    apiFetch<Paginated<ComplaintListItem>>(
      `/complaints${toQueryString(params as Record<string, unknown>)}`,
    ),
  detail: (id: number) => apiFetch<ComplaintDetail>(`/complaints/${id}`),
  create: (body: ComplaintCreateBody) =>
    apiFetch<ComplaintDetail>("/complaints", { method: "POST", body }),
  update: (id: number, body: ComplaintUpdateBody) =>
    apiFetch<ComplaintDetail>(`/complaints/${id}`, { method: "PATCH", body }),
  withdraw: (id: number) =>
    apiFetch<ComplaintDetail>(`/complaints/${id}/withdraw`, { method: "POST" }),
  addImage: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return apiFetch<ComplaintImage>(`/complaints/${id}/images`, {
      method: "POST",
      body: fd,
    });
  },
  deleteImage: (id: number, imageId: number) =>
    apiFetch<void>(`/complaints/${id}/images/${imageId}`, { method: "DELETE" }),
};

export const financeApi = {
  houseDues: (houseId: number) =>
    apiFetch<HouseDuesResponse>(`/finance/houses/${houseId}/dues`),
};

export const notificationsApi = {
  list: (page = 1, pageSize = 20) =>
    apiFetch<NotificationsResponse>(
      `/notifications?page=${page}&page_size=${pageSize}`,
    ),
  unreadCount: () =>
    apiFetch<{ unread_count: number }>("/notifications/unread-count"),
  markRead: (id: number) =>
    apiFetch<{ cleared: 0 | 1 }>(`/notifications/${id}/read`, {
      method: "POST",
    }),
  markAllRead: () =>
    apiFetch<{ cleared: number }>("/notifications/read-all", { method: "POST" }),
};
