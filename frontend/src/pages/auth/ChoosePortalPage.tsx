import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Home, Loader2, Wrench } from "lucide-react";
import type { Portal } from "@/types/auth";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/hooks/useAuth";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/format";

const PORTAL_META: Record<string, { label: string; icon: typeof Home }> = {
  resident: { label: "Resident", icon: Home },
  admin: { label: "Admin", icon: Wrench },
  platform: { label: "Platform", icon: Wrench },
};

export function ChoosePortalPage() {
  const { status, availablePortals, setPortal, me } = useAuth();
  const navigate = useNavigate();
  const [pending, setPending] = useState<Portal | null>(null);

  if (status === "anon") return <Navigate to="/login" replace />;
  if (status === "must_change") return <Navigate to="/change-password" replace />;

  const pick = async (p: Portal) => {
    try {
      setPending(p);
      await setPortal(p);
      // me.landing is resolved for the chosen portal after setPortal.
      navigate("/", { replace: true });
    } catch (e) {
      toast.error(getErrorMessage(e));
      setPending(null);
    }
  };

  const portals = availablePortals.length ? availablePortals : me?.available_portals ?? [];

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-semibold">Choose a portal</h1>
          <p className="text-sm text-muted-foreground">
            You have access to more than one portal.
          </p>
        </div>
        <div className="grid gap-3">
          {portals.map((p) => {
            const meta = PORTAL_META[p] ?? { label: p, icon: Home };
            const Icon = meta.icon;
            return (
              <Card
                key={p}
                className="cursor-pointer transition-colors hover:bg-accent"
                onClick={() => pending === null && void pick(p)}
              >
                <CardContent className="flex items-center gap-4 p-5">
                  <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    {pending === p ? (
                      <Loader2 className="h-5 w-5 animate-spin" />
                    ) : (
                      <Icon className="h-5 w-5" />
                    )}
                  </div>
                  <div>
                    <p className="font-medium">{meta.label}</p>
                    <p className="text-sm text-muted-foreground">
                      Enter the {meta.label.toLowerCase()} portal
                    </p>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>
    </div>
  );
}
