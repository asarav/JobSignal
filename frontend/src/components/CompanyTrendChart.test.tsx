import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompanyTrendChart } from "./CompanyTrendChart";

describe("CompanyTrendChart", () => {
  it("renders one bar per day and the day labels", () => {
    const { container } = render(
      <CompanyTrendChart
        points={[
          { day: "2026-09-01", n_unique_postings: 3, sample_tier: "limited" },
          { day: "2026-10-01", n_unique_postings: 8, sample_tier: "limited" },
        ]}
      />,
    );
    expect(container.querySelectorAll("rect")).toHaveLength(2);
    expect(screen.getByText("2026-09-01")).toBeTruthy();
    expect(screen.getByText("2026-10-01")).toBeTruthy();
  });

  it("withholds rates below the adequate tier", () => {
    render(
      <CompanyTrendChart
        points={[
          {
            day: "2026-09-01",
            n_unique_postings: 3,
            repost_ratio: 0.5,
            inactive_ratio: 0.4,
            sample_tier: "limited",
          },
        ]}
      />,
    );
    expect(screen.getByText(/Counts only/)).toBeTruthy();
    expect(screen.queryByText(/Latest rates/)).toBeNull();
  });

  it("shows rates when the sample is adequate", () => {
    render(
      <CompanyTrendChart
        points={[
          {
            day: "2026-09-01",
            n_unique_postings: 12,
            repost_ratio: 0.25,
            inactive_ratio: 0.1,
            sample_tier: "adequate",
          },
        ]}
      />,
    );
    expect(screen.getByText(/Latest rates/)).toBeTruthy();
  });

  it("shows the empty note when there is no history", () => {
    render(<CompanyTrendChart points={[]} note="No daily summaries yet." />);
    expect(screen.getByText("No daily summaries yet.")).toBeTruthy();
  });
});
