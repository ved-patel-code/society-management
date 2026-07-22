// Map a notification to an in-app route. entity_type: "complaint"|"notice"|"house"|null
export function notificationLinks(n: {
  entity_type: string | null;
  entity_id: number | null;
}): string {
  switch (n.entity_type) {
    case "notice":
      return n.entity_id ? `/notices/${n.entity_id}` : "/notices";
    case "complaint":
      return n.entity_id ? `/complaints/${n.entity_id}` : "/complaints";
    case "house":
      return "/finance"; // maintenance_due deep-links to the Financial page
    default:
      return "/notifications";
  }
}
