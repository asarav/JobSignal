import { describe, expect, it } from "vitest";
import { diffLines, summarizeDiff } from "./lineDiff";
import { orderedVersions, previousVersion, parsedText, type ResumeVersionRow } from "./resumeVersions";

describe("diffLines", () => {
  it("marks unchanged lines as equal", () => {
    const lines = diffLines("a\nb", "a\nb");
    expect(lines.map((line) => line.kind)).toEqual(["equal", "equal"]);
  });

  it("reports added and removed lines around stable text", () => {
    const lines = diffLines("a\nb\nc", "a\nB\nc\nd");
    expect(lines).toEqual([
      { kind: "equal", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "B" },
      { kind: "equal", text: "c" },
      { kind: "added", text: "d" },
    ]);
    expect(summarizeDiff(lines)).toEqual({ added: 2, removed: 1 });
  });

  it("treats every line as added when there is no previous text", () => {
    const lines = diffLines("", "one\ntwo");
    expect(lines.every((line) => line.kind === "added")).toBe(true);
    expect(summarizeDiff(lines)).toEqual({ added: 2, removed: 0 });
  });

  it("normalises CRLF line endings", () => {
    const lines = diffLines("a\r\nb", "a\nb");
    expect(summarizeDiff(lines)).toEqual({ added: 0, removed: 0 });
  });
});

describe("resume version helpers", () => {
  const rows: ResumeVersionRow[] = [
    { id: "v3", created_at: "2026-03-01T00:00:00Z" },
    { id: "v1", created_at: "2026-01-01T00:00:00Z" },
    { id: "v2", created_at: "2026-02-01T00:00:00Z" },
  ];

  it("orders versions oldest first", () => {
    expect(orderedVersions(rows).map((row) => row.id)).toEqual(["v1", "v2", "v3"]);
  });

  it("finds the version imported before the selected one", () => {
    expect(previousVersion(rows, "v3")?.id).toBe("v2");
    expect(previousVersion(rows, "v1")).toBeNull();
  });

  it("reads the stored parsed text and tolerates bad json", () => {
    expect(parsedText({ id: "v1", parsed_json: JSON.stringify({ text: "hello" }) })).toBe("hello");
    expect(parsedText({ id: "v1", parsed_json: "not json" })).toBeNull();
    expect(parsedText({ id: "v1", parsed_json: null })).toBeNull();
  });
});
