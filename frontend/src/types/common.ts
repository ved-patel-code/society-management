export interface ApiErrorShape {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorShape) {
    super(body?.message ?? "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "unknown_error";
    this.details = body?.details ?? {};
  }
}

export interface Paginated<T> {
  items: T[];
  total: number;
}
