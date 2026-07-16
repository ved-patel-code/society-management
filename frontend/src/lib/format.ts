import { ApiError } from "@/types/common";

const DASH = "—";

const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateOnlyFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const currencyFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
});

// Normalize the narrow / regular non-breaking spaces some ICU builds emit
// (U+202F, U+00A0) to a plain space so output matches "13 Jul 2026, 09:00".
const normalizeSpaces = (s: string): string =>
  s.replace(/[  ]/g, " ");

// "13 Jul 2026, 09:00" or "—"
export const fmtDate = (iso?: string | null): string => {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return normalizeSpaces(dateTimeFmt.format(d));
};

// "13 Jul 2026" or "—"
export const fmtDateOnly = (iso?: string | null): string => {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return normalizeSpaces(dateOnlyFmt.format(d));
};

// "₹2,500.00" or "—"
export const formatCurrency = (v?: string | number | null): string => {
  if (v === null || v === undefined || v === "") return DASH;
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isNaN(n)) return DASH;
  return currencyFmt.format(n);
};

export function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong. Please try again.";
}
