import { ChallengeCard } from "./ChallengesPage";
import type { Challenge } from "@/api/types";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

const baseChallenge: Challenge = {
  id: "c1",
  slug: "welcome",
  title: "Welcome",
  category: "Misc",
  description_md: "Test",
  connection_info: null,
  scoring_type: "fixed",
  current_points: 100,
  solve_count: 3,
  solved: false,
  max_attempts: 5,
  attempts: 1,
  visible_at: null,
  prerequisite_ids: [],
};

describe("ChallengeCard", () => {
  it("links to the challenge and exposes scoring context", () => {
    render(<MemoryRouter><ChallengeCard challenge={baseChallenge} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /Welcome/ })).toHaveAttribute("href", "/challenges/c1");
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("3 solves")).toBeInTheDocument();
    expect(screen.getByText("1/5회 시도")).toBeInTheDocument();
  });

  it("clearly marks an already solved challenge", () => {
    render(<MemoryRouter><ChallengeCard challenge={{ ...baseChallenge, solved: true }} /></MemoryRouter>);
    expect(screen.getByText("해결")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveClass("challenge-card--solved");
  });
});
