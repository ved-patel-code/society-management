import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { authApi } from "@/lib/api/endpoints";
import { tokenStore } from "@/lib/auth/tokenStore";
import { getErrorMessage } from "@/lib/format";
import { toast } from "sonner";

const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password."),
    new_password: z
      .string()
      .min(8, "Password must be at least 8 characters.")
      .regex(/[A-Za-z]/, "Password must contain at least one letter.")
      .regex(/[0-9]/, "Password must contain at least one digit."),
    confirm_password: z.string().min(1, "Confirm your new password."),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    path: ["confirm_password"],
    message: "Passwords do not match.",
  })
  .refine((v) => v.new_password !== v.current_password, {
    path: ["new_password"],
    message: "New password must be different from the current password.",
  });

type FormValues = z.infer<typeof schema>;

export function ChangePasswordPage() {
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      current_password: "",
      new_password: "",
      confirm_password: "",
    },
  });

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      await authApi.changePassword(values.current_password, values.new_password);
      // Server revoked all sessions — force a fresh login.
      tokenStore.clear();
      toast.success("Password changed. Please log in again.");
      navigate("/login", { replace: true });
    } catch (e) {
      setFormError(getErrorMessage(e));
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1">
          <CardTitle className="text-xl">Change your password</CardTitle>
          <CardDescription>
            For your security, set a new password to continue.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="current_password">Current password</Label>
              <Input
                id="current_password"
                type="password"
                autoComplete="current-password"
                {...register("current_password")}
              />
              {errors.current_password ? (
                <p className="text-sm text-destructive">
                  {errors.current_password.message}
                </p>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="new_password">New password</Label>
              <Input
                id="new_password"
                type="password"
                autoComplete="new-password"
                {...register("new_password")}
              />
              {errors.new_password ? (
                <p className="text-sm text-destructive">
                  {errors.new_password.message}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  At least 8 characters, with a letter and a digit.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirm_password">Confirm new password</Label>
              <Input
                id="confirm_password"
                type="password"
                autoComplete="new-password"
                {...register("confirm_password")}
              />
              {errors.confirm_password ? (
                <p className="text-sm text-destructive">
                  {errors.confirm_password.message}
                </p>
              ) : null}
            </div>

            {formError ? (
              <p className="text-sm text-destructive" role="alert">
                {formError}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Change password"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
