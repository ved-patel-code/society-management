// Shared shapes referenced by the stubbed financeApi + useHouseDues; the Finance session refines/extends.

export interface HouseDue {
  id: number;
  house_id: number;
  period_year: number;
  period_month: number; // 1-12
  amount_due: string; // decimal string
  due_date: string; // ISO date
  status: "outstanding" | "paid";
  source: "accrued" | "prepaid";
  locked_rate: string | null;
  paid_at: string | null;
  is_overdue: boolean;
}

export interface HouseDuesResponse {
  house_id: number;
  outstanding: HouseDue[]; // outstanding only, oldest-first
  outstanding_total: string;
  history: HouseDue[]; // all, oldest-first
}
