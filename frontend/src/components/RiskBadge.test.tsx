import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it("names the class and the confidence instead of a bare percentage", () => {
    render(<RiskBadge riskClass="likely_inactive" confidence={0.82} />);
    expect(screen.getAllByText("May be inactive").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("confidence 0.82")).toBeTruthy();
    expect(screen.getByText(/not a prediction about you/i)).toBeTruthy();
  });

  it("opens its explanation from the badge itself", () => {
    const { container } = render(
      <RiskBadge riskClass="likely_stale" confidence={0.4} label="Reposted twice" />,
    );
    expect(container.querySelector("details")).not.toBeNull();
    expect(screen.getByLabelText(/open the explanation/i)).toBeTruthy();
    expect(screen.getByText("Reposted twice")).toBeTruthy();
    expect(screen.getByText(/missing signal never raises risk/i)).toBeTruthy();
  });

  it("can show only the class in a list while keeping confidence in the explanation", () => {
    const { container } = render(
      <RiskBadge riskClass="likely_active" confidence={0.82} compact />,
    );
    expect(container.querySelector("summary")?.textContent).toBe("Recent activity");
    expect(container.textContent).toContain("confidence 0.82");
  });

  it("renders nothing when there is no evidence", () => {
    const { container } = render(<RiskBadge riskClass={null} confidence={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("says so when confidence is missing rather than implying certainty", () => {
    render(<RiskBadge riskClass="needs_verification" />);
    expect(screen.getByText("confidence unknown")).toBeTruthy();
  });
});
