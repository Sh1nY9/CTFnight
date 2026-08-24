import type { RegistrationCode, RegistrationCodeCreated } from "@/api/types";
import { ToastProvider } from "@/components/Toast";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminRegistrationCodes } from "./AdminRegistrationCodes";

const codeMocks = vi.hoisted(() => ({
  registrationCodes: vi.fn(),
  createRegistrationCode: vi.fn(),
  revokeRegistrationCode: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({ api: { admin: codeMocks } }));

const storedCode: RegistrationCode = {
  id: "code-1",
  event_id: "event-1",
  label: "초청 참가자",
  max_uses: 10,
  use_count: 2,
  expires_at: null,
  active: true,
  created_by: "admin-1",
  created_at: "2026-08-24T00:00:00Z",
  revoked_at: null,
};

function renderCodes(readOnly = false) {
  return render(<ToastProvider><AdminRegistrationCodes readOnly={readOnly} /></ToastProvider>);
}

describe("admin registration-code controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    codeMocks.registrationCodes.mockResolvedValue([]);
    codeMocks.revokeRegistrationCode.mockResolvedValue(undefined);
  });

  afterEach(() => vi.restoreAllMocks());

  it("creates an unlimited code and displays its plaintext only from the create response", async () => {
    const created: RegistrationCodeCreated = {
      ...storedCode,
      max_uses: null,
      use_count: 0,
      access_code: "one-time-registration-secret",
    };
    codeMocks.createRegistrationCode.mockResolvedValue(created);
    renderCodes();

    await screen.findByText("발급된 코드가 없습니다");
    fireEvent.change(screen.getByLabelText("라벨"), { target: { value: "  초청 참가자  " } });
    fireEvent.click(screen.getByLabelText("사용 횟수 무제한"));
    fireEvent.submit(screen.getByRole("button", { name: "코드 생성" }).closest("form")!);

    await waitFor(() => expect(codeMocks.createRegistrationCode).toHaveBeenCalledWith({
      label: "초청 참가자",
      max_uses: null,
      expires_at: null,
    }));
    expect(await screen.findByText("one-time-registration-secret")).toBeInTheDocument();
    expect(screen.getByText(/지금 한 번만 표시/)).toBeInTheDocument();
    expect(screen.getByText("0 / 무제한")).toBeInTheDocument();
  });

  it("requires confirmation, revokes a code, and removes any revealed plaintext", async () => {
    codeMocks.registrationCodes.mockResolvedValue([storedCode]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderCodes();

    fireEvent.click(await screen.findByRole("button", { name: "초청 참가자 코드 폐기" }));

    await waitFor(() => expect(codeMocks.revokeRegistrationCode).toHaveBeenCalledWith("code-1"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("다시 활성화할 수 없습니다"));
    expect(await screen.findByText("폐기됨")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "초청 참가자 코드 폐기" })).toBeDisabled();
  });

  it("keeps archived event codes read-only", async () => {
    codeMocks.registrationCodes.mockResolvedValue([storedCode]);
    renderCodes(true);

    await screen.findByText("보관된 이벤트의 코드는 열람만 할 수 있습니다.");
    expect(screen.queryByRole("button", { name: "코드 생성" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "초청 참가자 코드 폐기" })).toBeDisabled();
  });
});
