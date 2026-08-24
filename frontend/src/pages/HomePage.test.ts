import type { EventState, EventSummary } from "@/api/types";
import { describe, expect, it } from "vitest";
import { getHomePrimaryAction } from "./HomePage";

const event = (state: EventState): EventSummary => ({
  id: "event-1",
  name: "CTFnight",
  slug: "ctfnight",
  state,
});

describe("home primary action", () => {
  it("offers registration only while the event accepts registrations", () => {
    expect(getHomePrimaryAction(null, event("registration"))).toEqual({
      to: "/register",
      label: "참가 계정 만들기",
    });
  });

  it.each<EventState>(["draft", "live", "frozen", "ended", "archived"])(
    "sends signed-out visitors to login during %s",
    (state) => {
      expect(getHomePrimaryAction(null, event(state))).toEqual({ to: "/login", label: "로그인" });
    },
  );
});
