import type { AdminUser } from "@/api/types";
import { describe, expect, it } from "vitest";
import { countParticipantUsers } from "./AdminOverviewPage";

const user = (id: string, role: AdminUser["role"]): AdminUser => ({
  id,
  username: id,
  email: `${id}@example.test`,
  role,
  active: true,
});

describe("admin overview metrics", () => {
  it("does not include administrator accounts in the participant count", () => {
    expect(countParticipantUsers([
      user("player-1", "participant"),
      user("admin", "admin"),
      user("player-2", "participant"),
    ])).toBe(2);
  });
});
