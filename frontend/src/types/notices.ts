// Notices DTOs — verbatim against docs/api/notice-board.md. Residents only ever see "published".

export type NoticeStatus = "draft" | "published" | "withdrawn";

export interface NoticeListItem {
  id: number;
  title: string;
  status: NoticeStatus;
  is_pinned: boolean;
  published_at: string | null;
  expires_at: string | null;
  last_edited_at: string | null;
  attachment_count: number;
  is_read: boolean;
  created_at: string;
  updated_at: string;
}

export interface NoticeAttachment {
  id: number;
  vault_document_id: number;
  preview_url: string | null;
  download_url: string | null;
  created_at: string;
}

export interface NoticeDetail extends NoticeListItem {
  body: string; // sanitized HTML
  created_by: number;
  withdrawn_at: string | null;
  withdrawn_by: number | null;
  attachments: NoticeAttachment[];
}

export interface NoticeListResponse {
  items: NoticeListItem[];
  total: number;
  unread_count: number;
}
