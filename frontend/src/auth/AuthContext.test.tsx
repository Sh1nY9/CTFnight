import { ApiError } from "@/api/client";
import type { CurrentUser } from "@/api/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";

const authMocks = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({
  api: { auth: authMocks },
}));

const currentUser: CurrentUser = {
  id: "user-1",
  email: "player@example.test",
  username: "player",
  role: "participant",
  team: null,
};

function AuthProbe() {
  const { user, loading, logout } = useAuth();
  const [error, setError] = useState("");
  if (loading) return <span>loading</span>;
  return (
    <div>
      <span data-testid="identity">{user?.username ?? "signed-out"}</span>
      <button onClick={() => void logout().catch((reason: Error) => setError(reason.message))} type="button">
        logout
      </button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}

describe("AuthProvider logout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMocks.me.mockResolvedValue(currentUser);
    authMocks.logout.mockResolvedValue(undefined);
  });

  it("clears the local user after the server confirms logout", async () => {
    render(<AuthProvider><AuthProbe /></AuthProvider>);
    await screen.findByText("player");

    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(screen.getByTestId("identity")).toHaveTextContent("signed-out"));
  });

  it("also clears the local user when the server reports an already invalid session", async () => {
    authMocks.logout.mockRejectedValue(new ApiError("expired", 401, "invalid_session"));
    render(<AuthProvider><AuthProbe /></AuthProvider>);
    await screen.findByText("player");

    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(screen.getByTestId("identity")).toHaveTextContent("signed-out"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the local session and rejects when logout fails for another reason", async () => {
    authMocks.logout.mockRejectedValue(new ApiError("service unavailable", 503, "service_unavailable"));
    render(<AuthProvider><AuthProbe /></AuthProvider>);
    await screen.findByText("player");

    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await screen.findByRole("alert");
    expect(screen.getByTestId("identity")).toHaveTextContent("player");
  });
});
