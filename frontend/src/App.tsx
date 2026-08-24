import { AppShell, AdminLayout } from "@/components/AppShell";
import { RequireAdmin, RequireAuth } from "@/components/RouteGuards";
import { Navigate, Route, Routes } from "react-router-dom";
import { HomePage } from "@/pages/HomePage";
import { LoginPage, RegisterPage } from "@/pages/AuthPages";
import { ChallengesPage } from "@/pages/ChallengesPage";
import { ChallengeDetailPage } from "@/pages/ChallengeDetailPage";
import { ScoreboardPage } from "@/pages/ScoreboardPage";
import { TeamPage } from "@/pages/TeamPage";
import { AdminOverviewPage } from "@/pages/admin/AdminOverviewPage";
import { AdminChallengesPage } from "@/pages/admin/AdminChallengesPage";
import { AdminSubmissionsPage } from "@/pages/admin/AdminSubmissionsPage";
import { AdminSettingsPage } from "@/pages/admin/AdminSettingsPage";
import { AdminAnnouncementsPage } from "@/pages/admin/AdminAnnouncementsPage";
import { AdminUsersPage } from "@/pages/admin/AdminUsersPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { AccountSecurityPage } from "@/pages/AccountSecurityPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/scoreboard" element={<ScoreboardPage />} />

        <Route element={<RequireAuth />}>
          <Route path="/challenges" element={<ChallengesPage />} />
          <Route path="/challenges/:id" element={<ChallengeDetailPage />} />
          <Route path="/team" element={<TeamPage />} />
          <Route path="/account/security" element={<AccountSecurityPage />} />
        </Route>

        <Route element={<RequireAdmin />}>
          <Route path="/admin" element={<AdminLayout><AdminOverviewPage /></AdminLayout>} />
          <Route path="/admin/challenges" element={<AdminLayout><AdminChallengesPage /></AdminLayout>} />
          <Route path="/admin/submissions" element={<AdminLayout><AdminSubmissionsPage /></AdminLayout>} />
          <Route path="/admin/users" element={<AdminLayout><AdminUsersPage /></AdminLayout>} />
          <Route path="/admin/announcements" element={<AdminLayout><AdminAnnouncementsPage /></AdminLayout>} />
          <Route path="/admin/settings" element={<AdminLayout><AdminSettingsPage /></AdminLayout>} />
        </Route>

        <Route path="/home" element={<Navigate replace to="/" />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  );
}
