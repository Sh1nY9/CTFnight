import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown security contract", () => {
  it("does not turn raw HTML into executable DOM", () => {
    const { container } = render(
      <Markdown>{'<script>window.__pwned = true</script><img src=x onerror="window.__pwned=true">'}</Markdown>,
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain("<script");
  });

  it.each(["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)"])(
    "removes the dangerous link protocol %s",
    (url) => {
      render(<Markdown>{`[위험 링크](${url})`}</Markdown>);

      const link = screen.getByText("위험 링크").closest("a");
      expect(link).not.toBeInTheDocument();
    },
  );

  it("keeps safe links isolated from the opener", () => {
    render(<Markdown>{"[외부](https://example.test/path) [내부](/team)"}</Markdown>);

    for (const name of ["외부", "내부"]) {
      const link = screen.getByRole("link", { name: new RegExp(name) });
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
      expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    }
    expect(screen.getByRole("link", { name: /외부/ })).toHaveAttribute("href", "https://example.test/path");
    expect(screen.getByRole("link", { name: /내부/ })).toHaveAttribute("href", "/team");
  });

  it("removes dangerous image protocols", () => {
    const { container } = render(<Markdown>{"![위험 이미지](javascript:alert(1))"}</Markdown>);
    const image = container.querySelector("img");

    expect(image).not.toBeInTheDocument();
    expect(screen.getByText("위험 이미지")).toBeInTheDocument();
  });
});
