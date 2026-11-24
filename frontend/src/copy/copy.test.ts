import { describe, expect, it } from "vitest";
import { Copy } from "./copy";

describe("copy module", () => {
  it("ships cautious autofill copy", () => {
    expect(Copy.footer.toLowerCase()).toContain("never submits applications");
  });

  it("does not describe shipped screens in milestone language", () => {
    const strings: string[] = [];
    const collect = (value: unknown): void => {
      if (typeof value === "string") strings.push(value);
      else if (value && typeof value === "object") Object.values(value).forEach(collect);
    };
    collect(Copy);
    expect(strings.some((value) => /milestone/i.test(value))).toBe(false);
  });

  it("explains every navigation destination", () => {
    for (const description of Object.values(Copy.navDescriptions)) {
      expect(description.trim().length).toBeGreaterThan(10);
    }
    for (const explanation of Object.values(Copy.tooltips)) {
      expect(explanation.trim().length).toBeGreaterThan(10);
    }
  });
});
