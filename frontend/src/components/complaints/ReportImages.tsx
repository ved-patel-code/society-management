import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ImageOff, Loader2, Upload, X } from "lucide-react";
import type { ComplaintImage } from "@/types/complaints";
import { complaintsApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { ApiError } from "@/types/common";
import { getErrorMessage } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

const MAX_REPORT_IMAGES = 2;

interface ReportImagesProps {
  complaintId: number;
  images: ComplaintImage[];
  // The viewer is the raiser and the complaint is open — only then can images change.
  canManage: boolean;
}

export function ReportImages({
  complaintId,
  images,
  canManage,
}: ReportImagesProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingDelete, setPendingDelete] = useState<ComplaintImage | null>(
    null,
  );
  // Set when Vault module is disabled — hide all controls after that.
  const [vaultDisabled, setVaultDisabled] = useState(false);

  const reportImages = images.filter((i) => i.kind === "report");
  const proofImages = images.filter((i) => i.kind === "proof");
  const atLimit = reportImages.length >= MAX_REPORT_IMAGES;

  const addMutation = useMutation({
    mutationFn: (file: File) => complaintsApi.addImage(complaintId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.complaints.detail(complaintId),
      });
      toast.success("Photo added.");
    },
    onError: (e) => {
      if (
        e instanceof ApiError &&
        e.status === 403 &&
        e.code === "module_disabled"
      ) {
        setVaultDisabled(true);
        toast.error("Photo uploads are not available for this society.");
        return;
      }
      // 413 storage_quota_exceeded / 415 file_type_not_allowed / 409 limit — surface message.
      toast.error(getErrorMessage(e));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (imageId: number) =>
      complaintsApi.deleteImage(complaintId, imageId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.complaints.detail(complaintId),
      });
      toast.success("Photo removed.");
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so choosing the same file again re-fires change.
    e.target.value = "";
    if (file) addMutation.mutate(file);
  };

  const showControls = canManage && !vaultDisabled;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Report photos</h3>
        {showControls ? (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={handleFileChange}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={atLimit || addMutation.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {addMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <Upload className="mr-2 h-4 w-4" />
                  Add photo
                </>
              )}
            </Button>
          </>
        ) : null}
      </div>

      {atLimit && showControls ? (
        <p className="text-xs text-muted-foreground">
          You can attach up to {MAX_REPORT_IMAGES} report photos.
        </p>
      ) : null}

      {reportImages.length === 0 ? (
        <p className="text-sm text-muted-foreground">No report photos.</p>
      ) : (
        <ul className="flex flex-wrap gap-3">
          {reportImages.map((img) => (
            <li key={img.id} className="relative">
              <ImageThumb image={img} alt="Report photo" />
              {showControls ? (
                <button
                  type="button"
                  aria-label="Remove photo"
                  className="absolute -right-2 -top-2 rounded-full bg-destructive p-1 text-destructive-foreground shadow-sm hover:opacity-90"
                  onClick={() => setPendingDelete(img)}
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {proofImages.length > 0 ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Resolution photos</h3>
          <ul className="flex flex-wrap gap-3">
            {proofImages.map((img) => (
              <li key={img.id}>
                <ImageThumb image={img} alt="Resolution photo" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(o) => {
          if (!o) setPendingDelete(null);
        }}
        title="Remove this photo?"
        description="This will delete the report photo from the complaint."
        confirmLabel="Remove"
        destructive
        onConfirm={async () => {
          if (pendingDelete) {
            await deleteMutation.mutateAsync(pendingDelete.id);
            setPendingDelete(null);
          }
        }}
      />
    </div>
  );
}

function ImageThumb({ image, alt }: { image: ComplaintImage; alt: string }) {
  if (!image.preview_url) {
    return (
      <div
        className="flex h-24 w-24 flex-col items-center justify-center gap-1 rounded-md border bg-muted text-muted-foreground"
        title="Preview unavailable"
      >
        <ImageOff className="h-5 w-5" aria-hidden="true" />
        <span className="text-[10px]">No preview</span>
      </div>
    );
  }
  return (
    <img
      src={image.preview_url}
      alt={alt}
      className="h-24 w-24 rounded-md border object-cover"
    />
  );
}
