import { describe, expect, it } from "vitest";
import { escapeCsvCell, serializeCsv } from "./csv";

describe("CSV export safety", () => {
  it.each([
    "=HYPERLINK(\"https://evil.test\")",
    "+cmd|' /C calc'!A0",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(1+1)",
    "\t=1+1",
    "\r=1+1",
    "\n=1+1",
    "  =1+1",
  ])("neutralizes spreadsheet formula input %j", (value) => {
    const escaped = escapeCsvCell(value);
    expect(escaped.startsWith("\"'")).toBe(true);
    expect(escaped.endsWith("\"")).toBe(true);
  });

  it("quotes embedded delimiters and quotes without changing safe text", () => {
    expect(escapeCsvCell('ctfnight, "team"')).toBe('"ctfnight, ""team"""');
    expect(escapeCsvCell("ordinary text")).toBe('"ordinary text"');
    expect(escapeCsvCell(0)).toBe('"0"');
  });

  it("serializes rows with CRLF separators", () => {
    expect(serializeCsv([["a", "b"], ["c", "d"]])).toBe('"a","b"\r\n"c","d"');
  });
});
