import type { CurrentUser } from "@/api/types";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequireAdmin, RequireAuth } from "./RouteGuards";

const guardState = vi.hoisted(() => ({
  user: null as CurrentUser | null,
  loading: false,
}));

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => guardState }));

const participant: CurrentUser = {
  id: "user-1",
  email: "player@example.test",
  username: "player",
  role: "participant",
  team: null,
};

function LoginProbe() {
  const location = useLocation();
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
  return <span>login from {from ?? "none"}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<span>home</span>} />
        <Route path="/login" element={<LoginProbe />} />
        <Route path="/account/security" element={<span>security</span>} />
        <Route element={<RequireAuth />}>
          <Route path="/private" element={<span>private</span>} />
        </Route>
        <Route element={<RequireAdmin />}>
          <Route path="/admin" element={<span>admin</span>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("route guards", () => {
  beforeEach(() => {
    guardState.user = null;
    guardState.loading = false;
  });

  it("preserves only the internal protected location when sending a guest to login", () => {
    renderAt("/private");
    expect(screen.getByText("login from /private")).toBeInTheDocument();
  });

  it("allows an authenticated participant through RequireAuth", () => {
    guardState.user = participant;
    renderAt("/private");
    expect(screen.getByText("private")).toBeInTheDocument();
  });

  it("does not render a protected route before session loading finishes", () => {
    guardState.loading = true;
    renderAt("/private");
    expect(screen.getByText("세션을 확인하는 중")).toBeInTheDocument();
    expect(screen.queryByText("private")).not.toBeInTheDocument();
  });

  it("rejects a participant from admin routes", () => {
    guardState.user = participant;
    renderAt("/admin");
    expect(screen.getByText("home")).toBeInTheDocument();
  });

  it("forces an initial-password admin to the security page", () => {
    guardState.user = { ...participant, role: "admin", password_change_required: true };
    renderAt("/admin");
    expect(screen.getByText("security")).toBeInTheDocument();
  });

  it("allows a rotated-password admin through RequireAdmin", () => {
    guardState.user = { ...participant, role: "admin", password_change_required: false };
    renderAt("/admin");
    expect(screen.getByText("admin")).toBeInTheDocument();
  });
});
