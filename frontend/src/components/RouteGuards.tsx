import { useAuth } from "@/auth/AuthContext";
import { LoadingState } from "./States";
import { Navigate, Outlet, useLocation } from "react-router-dom";

export function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingState label="세션을 확인하는 중" />;
  if (!user) return <Navigate replace state={{ from: location }} to="/login" />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingState label="권한을 확인하는 중" />;
  if (!user) return <Navigate replace state={{ from: location }} to="/login" />;
  if (user.password_change_required) return <Navigate replace to="/account/security" />;
  if (user.role !== "admin") return <Navigate replace to="/" />;
  return <Outlet />;
}
