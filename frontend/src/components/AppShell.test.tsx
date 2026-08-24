import { ApiError } from "@/api/client";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

const shellMocks = vi.hoisted(() => ({
  logout: vi.fn(),
  push: vi.fn(),
  user: {
    id: "user-1",
    email: "player@example.test",
    username: "player",
    role: "participant" as const,
    team: null,
  },
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: shellMocks.user, logout: shellMocks.logout }),
}));

vi.mock("@/components/Toast", () => ({
  useToast: () => ({ push: shellMocks.push }),
}));

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>;
}

describe("AppShell logout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    shellMocks.logout.mockResolvedValue(undefined);
  });

  it("keeps the user on the current page and reports a failed logout", async () => {
    shellMocks.logout.mockRejectedValue(new ApiError("service unavailable", 503, "service_unavailable"));
    render(
      <MemoryRouter initialEntries={["/team"]}>
        <AppShell><LocationProbe /></AppShell>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));

    await waitFor(() => expect(shellMocks.push).toHaveBeenCalledWith(
      "로그아웃하지 못했습니다. service unavailable",
      "error",
    ));
    expect(screen.getByTestId("location")).toHaveTextContent("/team");
    expect(screen.getByText("player")).toBeInTheDocument();
  });

  it("renders the exact CTFnight product brand", () => {
    render(
      <MemoryRouter>
        <AppShell><LocationProbe /></AppShell>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "CTFnight 홈" })).toHaveTextContent("CTFnight");
    expect(screen.getByText("CTFnight", { selector: "footer span" })).toBeInTheDocument();
  });
});
