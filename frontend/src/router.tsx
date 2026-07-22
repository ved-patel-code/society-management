import { createBrowserRouter, Navigate } from "react-router-dom";
import { RequireAuth } from "@/lib/auth/guards";
import { AppShell } from "@/components/shell/AppShell";
import { LoadingState } from "@/components/common/LoadingState";
import { RootRedirect } from "@/pages/RootRedirect";
import { LoginPage } from "@/pages/auth/LoginPage";
import { ChoosePortalPage } from "@/pages/auth/ChoosePortalPage";
import { ChangePasswordPage } from "@/pages/auth/ChangePasswordPage";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { ComplaintsPage } from "@/pages/complaints/ComplaintsPage";
import { ComplaintDetailPage } from "@/pages/complaints/ComplaintDetailPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/choose-portal", element: <ChoosePortalPage /> },
  { path: "/change-password", element: <ChangePasswordPage /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { path: "/", element: <RootRedirect /> },
      { path: "/notices", element: <LoadingState /> }, // replaced by Notices session
      { path: "/notices/:id", element: <LoadingState /> },
      { path: "/finance", element: <LoadingState /> }, // replaced by Finance session
      { path: "/complaints", element: <ComplaintsPage /> },
      { path: "/complaints/:id", element: <ComplaintDetailPage /> },
      { path: "/notifications", element: <LoadingState /> }, // replaced by Notifications session
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
