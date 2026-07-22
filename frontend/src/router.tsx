import { createBrowserRouter, Navigate } from "react-router-dom";
import { RequireAuth } from "@/lib/auth/guards";
import { AppShell } from "@/components/shell/AppShell";
import { LoadingState } from "@/components/common/LoadingState";
import { RootRedirect } from "@/pages/RootRedirect";
import { LoginPage } from "@/pages/auth/LoginPage";
import { ChoosePortalPage } from "@/pages/auth/ChoosePortalPage";
import { ChangePasswordPage } from "@/pages/auth/ChangePasswordPage";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { NotificationsPage } from "@/pages/notifications/NotificationsPage";

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
      { path: "/complaints", element: <LoadingState /> }, // replaced by Complaints session
      { path: "/complaints/:id", element: <LoadingState /> },
      { path: "/notifications", element: <NotificationsPage /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
