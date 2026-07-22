import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Undo2 } from "lucide-react";
import { complaintsApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/types/common";
import { fmtDate } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { SectionCard } from "@/components/common/SectionCard";
import { EmptyState } from "@/components/common/EmptyState";
import { LoadingState } from "@/components/common/LoadingState";
import { Forbidden } from "@/components/common/Forbidden";
import { Can } from "@/components/common/Can";
import { IfModule } from "@/components/common/IfModule";
import { StatusBadge } from "@/components/common/StatusBadge";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { ComplaintTimeline } from "@/components/complaints/ComplaintTimeline";
import { ReportImages } from "@/components/complaints/ReportImages";
import { EditComplaintModal } from "@/components/complaints/EditComplaintModal";

export function ComplaintDetailPage() {
  return (
    <IfModule module="complaints" fallback={<Forbidden />}>
      <ComplaintDetailPageInner />
    </IfModule>
  );
}

function ComplaintDetailPageInner() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { me } = useAuth();

  const [editOpen, setEditOpen] = useState(false);
  const [withdrawOpen, setWithdrawOpen] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: queryKeys.complaints.detail(id),
    queryFn: () => complaintsApi.detail(id),
    enabled: Number.isFinite(id),
  });

  // Reading a complaint clears its notification server-side -> refresh the bell.
  useEffect(() => {
    if (data) {
      queryClient.invalidateQueries({
        queryKey: queryKeys.notifications.unread(),
      });
    }
  }, [data, queryClient]);

  const withdrawMutation = useMutation({
    mutationFn: () => complaintsApi.withdraw(id),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.complaints.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: ["complaints", "list"] });
    },
  });

  if (!Number.isFinite(id)) {
    return <EmptyState title="Complaint not found" />;
  }

  if (isLoading) return <LoadingState />;

  if (isError) {
    if (error instanceof ApiError) {
      if (error.status === 404) {
        return (
          <div className="space-y-6">
            <BackButton onClick={() => navigate("/complaints")} />
            <EmptyState
              title="Complaint not found"
              description="It may have been removed, or you may not have access to it."
            />
          </div>
        );
      }
      if (error.status === 403) return <Forbidden />;
    }
    return (
      <div className="space-y-6">
        <BackButton onClick={() => navigate("/complaints")} />
        <EmptyState title="Couldn't load this complaint" />
      </div>
    );
  }

  if (!data) return null;

  const isRaiser = me?.user.id === data.raised_by;
  const isOpen = data.status === "open";
  const canManage = isRaiser && isOpen;

  return (
    <div className="space-y-6">
      <BackButton onClick={() => navigate("/complaints")} />

      <PageHeader
        title={data.title}
        description={`${data.reference} · ${data.house_display_code ?? "—"} · ${data.category_name}`}
        actions={<StatusBadge status={data.status} />}
      />

      {canManage ? (
        <Can permission="complaints.create">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => setEditOpen(true)}
            >
              <Pencil className="mr-2 h-4 w-4" />
              Edit
            </Button>
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => setWithdrawOpen(true)}
            >
              <Undo2 className="mr-2 h-4 w-4" />
              Withdraw
            </Button>
          </div>
        </Can>
      ) : null}

      <SectionCard title="Details">
        <p className="whitespace-pre-wrap text-sm">{data.description}</p>
        <p className="mt-4 text-xs text-muted-foreground">
          Raised {fmtDate(data.created_at)} · Last updated{" "}
          {fmtDate(data.updated_at)}
        </p>
      </SectionCard>

      <SectionCard title="Photos">
        <ReportImages
          complaintId={data.id}
          images={data.images}
          canManage={canManage}
        />
      </SectionCard>

      <SectionCard title="History">
        <ComplaintTimeline entries={data.timeline} />
      </SectionCard>

      {isRaiser && isOpen ? (
        <EditComplaintModal
          open={editOpen}
          onOpenChange={setEditOpen}
          complaint={data}
        />
      ) : null}

      <ConfirmDialog
        open={withdrawOpen}
        onOpenChange={setWithdrawOpen}
        title="Withdraw this complaint?"
        description="Withdrawing closes the complaint. This can't be undone."
        confirmLabel="Withdraw"
        destructive
        onConfirm={async () => {
          await withdrawMutation.mutateAsync();
        }}
      />
    </div>
  );
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" className="-ml-2" onClick={onClick}>
      <ArrowLeft className="mr-2 h-4 w-4" />
      Back to complaints
    </Button>
  );
}
