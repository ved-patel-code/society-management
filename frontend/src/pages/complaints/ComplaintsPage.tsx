import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import type { ComplaintListParams } from "@/hooks/useComplaints";
import { useComplaints, useComplaintCategories } from "@/hooks/useComplaints";
import { ApiError } from "@/types/common";
import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Forbidden } from "@/components/common/Forbidden";
import { Can } from "@/components/common/Can";
import { IfModule } from "@/components/common/IfModule";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getErrorMessage } from "@/lib/format";
import { StatusFilter } from "@/components/complaints/StatusFilter";
import { ComplaintTable } from "@/components/complaints/ComplaintTable";
import { RaiseComplaintModal } from "@/components/complaints/RaiseComplaintModal";

const PAGE_SIZE = 20;
const ALL_CATEGORIES = "__all__";

export function ComplaintsPage() {
  return (
    <IfModule module="complaints" fallback={<Forbidden />}>
      <ComplaintsPageInner />
    </IfModule>
  );
}

function ComplaintsPageInner() {
  const navigate = useNavigate();
  const { data: categories = [] } = useComplaintCategories();

  const [status, setStatus] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const [raiseOpen, setRaiseOpen] = useState(false);

  // Debounce the free-text search.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Any filter change resets to the first page.
  useEffect(() => {
    setPage(1);
  }, [status, categoryId, dateFrom, dateTo, debouncedSearch]);

  const params = useMemo<ComplaintListParams>(
    () => ({
      page,
      page_size: PAGE_SIZE,
      status: status || undefined,
      category_id: categoryId ? Number(categoryId) : undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      q: debouncedSearch || undefined,
    }),
    [page, status, categoryId, dateFrom, dateTo, debouncedSearch],
  );

  const { data, isLoading, isError, error } = useComplaints(params);

  if (isError && error instanceof ApiError && error.status === 403) {
    return <Forbidden />;
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters =
    status !== "" ||
    categoryId !== "" ||
    dateFrom !== "" ||
    dateTo !== "" ||
    debouncedSearch !== "";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Complaints"
        description="Track issues raised for your house."
        actions={
          <Can permission="complaints.create">
            <Button onClick={() => setRaiseOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Raise complaint
            </Button>
          </Can>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="space-y-1.5">
          <Label htmlFor="filter-search">Search</Label>
          <Input
            id="filter-search"
            placeholder="Reference or title"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            maxLength={100}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filter-status">Status</Label>
          <StatusFilter value={status} onChange={setStatus} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filter-category">Category</Label>
          <Select
            value={categoryId === "" ? ALL_CATEGORIES : categoryId}
            onValueChange={(v) =>
              setCategoryId(v === ALL_CATEGORIES ? "" : v)
            }
          >
            <SelectTrigger id="filter-category" aria-label="Filter by category">
              <SelectValue placeholder="All categories" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_CATEGORIES}>All categories</SelectItem>
              {categories.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filter-from">From</Label>
          <Input
            id="filter-from"
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filter-to">To</Label>
          <Input
            id="filter-to"
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : isError ? (
        <EmptyState
          title="Couldn't load complaints"
          description={getErrorMessage(error)}
        />
      ) : (
        <>
          <ComplaintTable
            rows={items}
            onRowClick={(row) => navigate(`/complaints/${row.id}`)}
            empty={
              <EmptyState
                title={hasFilters ? "No matching complaints" : "No complaints yet"}
                description={
                  hasFilters
                    ? "Try adjusting your filters."
                    : "Raise one to get started."
                }
              />
            }
          />

          {total > PAGE_SIZE ? (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Page {page} of {totalPages} · {total} total
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  Next
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}

      <RaiseComplaintModal
        open={raiseOpen}
        onOpenChange={setRaiseOpen}
        onCreated={(c) => navigate(`/complaints/${c.id}`)}
      />
    </div>
  );
}
