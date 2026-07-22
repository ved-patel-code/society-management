// Shared shapes referenced by the stubbed notificationsApi + useUnreadNotifications + notificationLinks;
// the Notifications session refines/extends.

export type NotificationType =
  | "complaint_new"
  | "complaint_update"
  | "complaint_withdrawn"
  | "notice"
  | "maintenance_due";

export type NotificationEntityType = "complaint" | "notice" | "house" | null;

export interface AppNotification {
  id: number;
  type: NotificationType;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  entity_type: NotificationEntityType;
  entity_id: number | null;
  created_at: string;
}

export interface NotificationsResponse {
  items: AppNotification[];
  unread_count: number;
  page: number;
  page_size: number;
}
