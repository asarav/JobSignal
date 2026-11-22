import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InfoTip } from "./InfoTip";

describe("InfoTip", () => {
  it("keeps its explanation hidden until asked for", () => {
    render(<InfoTip label="Sources">Paste a career page URL.</InfoTip>);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("shows the explanation on click and associates it with the button", () => {
    render(<InfoTip label="Sources">Paste a career page URL.</InfoTip>);

    const trigger = screen.getByRole("button", { name: "About Sources" });
    fireEvent.click(trigger);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Paste a career page URL.");
    expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);

    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("opens on keyboard focus and closes on blur", () => {
    render(<InfoTip label="Sources">Paste a career page URL.</InfoTip>);
    const trigger = screen.getByRole("button", { name: "About Sources" });

    fireEvent.focus(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    fireEvent.blur(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
