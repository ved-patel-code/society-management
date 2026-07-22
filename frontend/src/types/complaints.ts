// Shared shapes referenced by the stubbed complaintsApi + useComplaints; the Complaints session refines/extends.

export type ComplaintStatus =
  | "open"
  | "in_progress"
  | "resolved"
  | "closed"
  | "archived"
  | "withdrawn";

export interface Category {
  id: number;
  name: string;
  is_active: boolean;
  is_system: boolean;
}

export interface StatusHistory {
  id: number;
  from_status: ComplaintStatus | null;
  to_status: ComplaintStatus;
  note: string | null;
  changed_by: number | null;
  created_at: string;
}

export interface ComplaintImage {
  id: number;
  kind: "report" | "proof";
  vault_document_id: number;
  preview_url: string | null;
  created_at: string;
}

export interface ComplaintListItem {
  id: number;
  reference: string;
  title: string;
  status: ComplaintStatus;
  category_id: number;
  category_name: string;
  house_id: number;
  house_display_code: string | null;
  report_image_count: number;
  proof_image_count: number;
  created_at: string;
  updated_at: string;
}

export interface ComplaintDetail {
  id: number;
  reference: string;
  house_id: number;
  house_display_code: string | null;
  raised_by: number;
  category_id: number;
  category_name: string;
  title: string;
  description: string;
  status: ComplaintStatus;
  resolved_at: string | null;
  closed_at: string | null;
  archived_at: string | null;
  withdrawn_at: string | null;
  created_at: string;
  updated_at: string;
  timeline: StatusHistory[];
  images: ComplaintImage[];
}

export interface ComplaintCreateBody {
  category_id: number;
  title: string;
  description: string;
  house_id?: number;
}

export interface ComplaintUpdateBody {
  title?: string;
  description?: string;
  category_id?: number;
}
