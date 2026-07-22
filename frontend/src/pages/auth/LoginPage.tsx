import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Building2, Loader2 } from "lucide-react";
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
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/types/common";
import { getErrorMessage } from "@/lib/format";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Password is required."),
});
type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const { status, me, login } = useAuth();
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  // Already authenticated -> bounce to the right place.
  if (status === "authed" && me) {
    // Multi-portal user with no portal chosen yet -> chooser (mirror RequireAuth).
    if (me.active_portal === null && me.available_portals.length > 1) {
      return <Navigate to="/choose-portal" replace />;
    }
    const landing = me.landing ? `/${me.landing}` : "/notices";
    return <Navigate to={landing} replace />;
  }
  if (status === "must_change") return <Navigate to="/change-password" replace />;

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      const res = await login(values.email, values.password);
      if (res.password_state === "must_change") {
        navigate("/change-password", { replace: true });
        return;
      }
      if (res.available_portals.length > 1) {
        navigate("/choose-portal", { replace: true });
      } else {
        // single portal: me() has been fetched by setPortal; land there.
        navigate("/", { replace: true });
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setFormError("Invalid email or password.");
      } else {
        setFormError(getErrorMessage(e));
      }
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1 text-center">
          <div className="mx-auto flex items-center gap-2">
            <Building2 className="h-6 w-6 text-primary" />
            <span className="text-lg font-semibold">Society Management</span>
          </div>
          <CardTitle className="pt-2 text-xl">Sign in</CardTitle>
          <CardDescription>Welcome back to your resident portal.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                autoFocus
                {...register("email")}
              />
              {errors.email ? (
                <p className="text-sm text-destructive">{errors.email.message}</p>
              ) : null}
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                <Link
                  to="/forgot-password"
                  className="text-xs text-muted-foreground underline-offset-4 hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                {...register("password")}
              />
              {errors.password ? (
                <p className="text-sm text-destructive">
                  {errors.password.message}
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
                "Sign in"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
