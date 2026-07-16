export const queryKeys = {
  me: (portal: string | null) => ["me", portal] as const,
  notices: {
    list: (page: number) => ["notices", "list", page] as const,
    detail: (id: number) => ["notices", "detail", id] as const,
  },
  complaints: {
    list: (params: Record<string, unknown>) =>
      ["complaints", "list", params] as const,
    detail: (id: number) => ["complaints", "detail", id] as const,
    categories: () => ["complaints", "categories"] as const,
  },
  finance: {
    dues: (houseId: number) => ["finance", "dues", houseId] as const,
  },
  notifications: {
    list: (page: number) => ["notifications", "list", page] as const,
    unread: () => ["notifications", "unread"] as const,
  },
};
