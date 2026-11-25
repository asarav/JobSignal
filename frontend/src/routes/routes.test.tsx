import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppShell } from "../components/AppShell";
import { AppRoutes, navigationGroups, utilityNavigation } from "./routes";

describe("routes", () => {
  it("opens on Today", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /today/i })).toBeInTheDocument();
  });

  it("renders the settings screen with sources first and plugins on their tab", async () => {
    render(
      <MemoryRouter initialEntries={["/settings?tab=plugins"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("extract.demo")).toBeInTheDocument());
  });

  it("exposes the Insights group and the Settings/Diagnostics footer", () => {
    const insights = navigationGroups.find((group) => group.label === "Insights");
    expect(insights?.collapsible).toBe(true);
    expect(insights?.items.map((item) => item.to)).toEqual([
      "/companies",
      "/posting-intelligence",
      "/keywords",
      "/seen",
    ]);
    expect(utilityNavigation.map((item) => item.to)).toEqual(["/settings", "/diagnostics"]);
  });

  it.each([
    ["/companies", /companies/i],
    ["/posting-intelligence", /posting intelligence/i],
    ["/keywords", /keyword insights/i],
    ["/seen", /previously seen/i],
    ["/settings", /settings/i],
    ["/diagnostics", /diagnostics/i],
  ])("renders %s inside the shell", async (path, heading) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <AppShell>
          <AppRoutes />
        </AppShell>
      </MemoryRouter>,
    );

    expect((await screen.findAllByRole("heading", { name: heading })).length).toBeGreaterThan(0);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Settings" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Diagnostics" }).length).toBeGreaterThan(0);
  });

  it("renders Find jobs and sends the old search route there", () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={["/discover"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /find jobs/i })).toBeInTheDocument();
    unmount();

    render(
      <MemoryRouter initialEntries={["/search-profiles"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /find jobs/i })).toBeInTheDocument();
  });
});
