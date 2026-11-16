import { describe, expect, it } from "vitest";
import { Copy } from "./copy";

describe("copy module", () => {
  it("declares the M0 not-implemented scope", () => {
    const joined = Copy.home.notImplementedList.join(" ").toLowerCase();
    for (const term of ["crawl", "ats", "resume", "matching", "ghost", "browser"]) {
      expect(joined).toContain(term);
    }
  });

  it("ships cautious autofill copy", () => {
    expect(Copy.footer.toLowerCase()).toContain("never submits applications");
  });
});
