import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { JobDescription } from "./JobDescription";

const payloads = JSON.parse(
  readFileSync(join(process.cwd(), "..", "tests", "fixtures", "xss", "payloads.json"), "utf8"),
) as { payloads: string[] };

describe("JobDescription", () => {
  it.each(payloads.payloads)("renders %s as inert text", (payload) => {
    const { container } = render(<JobDescription value={payload} />);
    expect(container.querySelector("script, img, svg, iframe, a")).toBeNull();
    expect(container.textContent).toContain(payload);
    expect((window as unknown as { __xss?: boolean }).__xss).toBeUndefined();
  });

  it("keeps the empty state", () => {
    const { container } = render(<JobDescription value={null} />);
    expect(container.textContent).toContain("No description was stored");
  });
});
