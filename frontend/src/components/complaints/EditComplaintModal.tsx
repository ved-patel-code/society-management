import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type {
  ComplaintDetail,
  ComplaintUpdateBody,
} from "@/types/complaints";
import { complaintsApi } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/api/queryKeys";
import { useComplaintCategories } from "@/hooks/useComplaints";
import { getErrorMessage } from "@/lib/format";
import { FormModal } from "@/components/common/FormModal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const schema = z.object({
  category_id: z.string().min(1, "Choose a category."),
  title: z
    .string()
    .trim()
    .min(1, "Title is required.")
    .max(200, "Title must be at most 200 characters."),
  description: z
    .string()
    .trim()
    .min(1, "Description is required.")
    .max(5000, "Description must be at most 5000 characters."),
});
type FormValues = z.infer<typeof schema>;

interface EditComplaintModalProps {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  complaint: ComplaintDetail;
}

export function EditComplaintModal({
  open,
  onOpenChange,
  complaint,
}: EditComplaintModalProps) {
  const queryClient = useQueryClient();
  const { data: categories = [] } = useComplaintCategories();

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      category_id: String(complaint.category_id),
      title: complaint.title,
      description: complaint.description,
    },
  });

  // Re-seed the form each time it opens for the current complaint.
  useEffect(() => {
    if (open) {
      reset({
        category_id: String(complaint.category_id),
        title: complaint.title,
        description: complaint.description,
      });
    }
  }, [open, complaint, reset]);

  const updateMutation = useMutation({
    mutationFn: (body: ComplaintUpdateBody) =>
      complaintsApi.update(complaint.id, body),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        queryKeys.complaints.detail(complaint.id),
        updated,
      );
      queryClient.invalidateQueries({
        queryKey: queryKeys.complaints.detail(complaint.id),
      });
      queryClient.invalidateQueries({ queryKey: ["complaints", "list"] });
      toast.success("Complaint updated.");
      onOpenChange(false);
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const onSubmit = (values: FormValues) => {
    // Send only changed fields; backend requires at least one.
    const body: ComplaintUpdateBody = {};
    const nextCategory = Number(values.category_id);
    if (nextCategory !== complaint.category_id) body.category_id = nextCategory;
    const nextTitle = values.title.trim();
    if (nextTitle !== complaint.title) body.title = nextTitle;
    const nextDesc = values.description.trim();
    if (nextDesc !== complaint.description) body.description = nextDesc;

    if (Object.keys(body).length === 0) {
      toast.error("Change at least one field to save.");
      return;
    }
    updateMutation.mutate(body);
  };

  const categoryValue = watch("category_id");
  const pending = updateMutation.isPending;

  return (
    <FormModal
      open={open}
      onOpenChange={onOpenChange}
      title="Edit complaint"
      description="You can edit a complaint only while it is open."
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button type="submit" form="edit-complaint-form" disabled={pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
          </Button>
        </>
      }
    >
      <form
        id="edit-complaint-form"
        onSubmit={handleSubmit(onSubmit)}
        className="space-y-4"
        noValidate
      >
        <div className="space-y-2">
          <Label htmlFor="edit-category_id">Category</Label>
          <Select
            value={categoryValue}
            onValueChange={(v) =>
              setValue("category_id", v, { shouldValidate: true })
            }
          >
            <SelectTrigger id="edit-category_id">
              <SelectValue placeholder="Choose a category" />
            </SelectTrigger>
            <SelectContent>
              {categories.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.category_id ? (
            <p className="text-sm text-destructive">
              {errors.category_id.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="edit-title">Title</Label>
          <Input id="edit-title" maxLength={200} {...register("title")} />
          {errors.title ? (
            <p className="text-sm text-destructive">{errors.title.message}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="edit-description">Description</Label>
          <Textarea
            id="edit-description"
            rows={5}
            maxLength={5000}
            {...register("description")}
          />
          {errors.description ? (
            <p className="text-sm text-destructive">
              {errors.description.message}
            </p>
          ) : null}
        </div>
      </form>
    </FormModal>
  );
}
