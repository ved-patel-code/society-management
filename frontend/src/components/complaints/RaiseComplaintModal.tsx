import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type {
  ComplaintCreateBody,
  ComplaintDetail,
} from "@/types/complaints";
import { complaintsApi } from "@/lib/api/endpoints";
import { useComplaintCategories } from "@/hooks/useComplaints";
import { ApiError } from "@/types/common";
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
  category_id: z
    .string()
    .min(1, "Choose a category.")
    .refine((v) => Number.isFinite(Number(v)), "Choose a category."),
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

interface RaiseComplaintModalProps {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated?: (complaint: ComplaintDetail) => void;
}

export function RaiseComplaintModal({
  open,
  onOpenChange,
  onCreated,
}: RaiseComplaintModalProps) {
  const queryClient = useQueryClient();
  const { data: categories = [] } = useComplaintCategories();
  // Populated only after a 422 "several houses" response.
  const [ownedHouseIds, setOwnedHouseIds] = useState<number[] | null>(null);
  const [houseId, setHouseId] = useState<string>("");
  const [houseError, setHouseError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { category_id: "", title: "", description: "" },
  });

  // Reset all state whenever the modal is (re)opened.
  useEffect(() => {
    if (open) {
      reset({ category_id: "", title: "", description: "" });
      setOwnedHouseIds(null);
      setHouseId("");
      setHouseError(null);
    }
  }, [open, reset]);

  const createMutation = useMutation({
    mutationFn: (body: ComplaintCreateBody) => complaintsApi.create(body),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["complaints", "list"] });
      toast.success("Complaint raised.");
      onOpenChange(false);
      onCreated?.(created);
    },
    onError: (e) => {
      // Multi-house owner: re-open the form with a house picker.
      if (
        e instanceof ApiError &&
        e.status === 422 &&
        Array.isArray(e.details.owned_house_ids)
      ) {
        const ids = (e.details.owned_house_ids as unknown[])
          .map((v) => Number(v))
          .filter((n) => Number.isFinite(n));
        setOwnedHouseIds(ids);
        setHouseError("You own several houses. Choose which one this is for.");
        return;
      }
      toast.error(getErrorMessage(e));
    },
  });

  const onSubmit = (values: FormValues) => {
    setHouseError(null);
    // If the picker is showing, a house must be chosen.
    if (ownedHouseIds && houseId === "") {
      setHouseError("Select a house.");
      return;
    }
    const body: ComplaintCreateBody = {
      category_id: Number(values.category_id),
      title: values.title.trim(),
      description: values.description.trim(),
    };
    if (ownedHouseIds && houseId !== "") body.house_id = Number(houseId);
    createMutation.mutate(body);
  };

  const categoryValue = watch("category_id");
  const pending = createMutation.isPending;

  return (
    <FormModal
      open={open}
      onOpenChange={onOpenChange}
      title="Raise a complaint"
      description="Describe the issue for your house."
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
          <Button
            type="submit"
            form="raise-complaint-form"
            disabled={pending}
          >
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Submit"}
          </Button>
        </>
      }
    >
      <form
        id="raise-complaint-form"
        onSubmit={handleSubmit(onSubmit)}
        className="space-y-4"
        noValidate
      >
        <div className="space-y-2">
          <Label htmlFor="category_id">Category</Label>
          <Select
            value={categoryValue}
            onValueChange={(v) =>
              setValue("category_id", v, { shouldValidate: true })
            }
          >
            <SelectTrigger id="category_id">
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
          <Label htmlFor="title">Title</Label>
          <Input id="title" maxLength={200} {...register("title")} />
          {errors.title ? (
            <p className="text-sm text-destructive">{errors.title.message}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
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

        {ownedHouseIds ? (
          <div className="space-y-2">
            <Label htmlFor="house_id">House</Label>
            <Select value={houseId} onValueChange={setHouseId}>
              <SelectTrigger id="house_id">
                <SelectValue placeholder="Choose a house" />
              </SelectTrigger>
              <SelectContent>
                {ownedHouseIds.map((id) => (
                  <SelectItem key={id} value={String(id)}>
                    House #{id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}

        {houseError ? (
          <p className="text-sm text-destructive" role="alert">
            {houseError}
          </p>
        ) : null}
      </form>
    </FormModal>
  );
}
