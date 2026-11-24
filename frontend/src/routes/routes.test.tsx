import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "./routes";

describe("routes", () => {
  it("renders the dashboard", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /dashboard/i })).toBeInTheDocument();
  });

  it("renders the settings screen with the builtin plugin", async () => {
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("extract.demo")).toBeInTheDocument());
  });

  it("keeps unimplemented screens empty but routed", () => {
    render(
      <MemoryRouter initialEntries={["/discover"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /find jobs/i })).toBeInTheDocument();
  });
});
