import type { ComponentType } from "react";
import { Megaphone, RefreshCw, Undo2, Wallet, Wrench } from "lucide-react";
import type { NotificationType } from "@/types/notifications";

interface NotificationMeta {
  icon: ComponentType<{ className?: string }>;
  label: string;
}

// Icon + human label per NotificationType (icons per module spec §3).
const META: Record<NotificationType, NotificationMeta> = {
  complaint_new: { icon: Wrench, label: "New complaint" },
  complaint_update: { icon: RefreshCw, label: "Complaint update" },
  complaint_withdrawn: { icon: Undo2, label: "Complaint withdrawn" },
  notice: { icon: Megaphone, label: "Notice" },
  maintenance_due: { icon: Wallet, label: "Maintenance due" },
};

const FALLBACK: NotificationMeta = { icon: Megaphone, label: "Notification" };

export function notificationMeta(type: NotificationType): NotificationMeta {
  return META[type] ?? FALLBACK;
}
