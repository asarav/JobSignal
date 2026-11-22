import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsRoute } from "./SettingsRoute";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listPlugins: vi.fn(),
  listSources: vi.fn(),
  updateSettings: vi.fn(),
}));

vi.mock("../../services/workerClient", () => ({
  workerClient: mocks,
  WorkerError: class WorkerError extends Error {
    readonly code = "test.error";
    readonly retryable = false;
  },
}));

function prime(snippets: Record<string, string>) {
  const config = { autofill: { snippets } };
  mocks.getSettings.mockResolvedValue({ config, overrides: {}, schema_version: 1 });
  mocks.listPlugins.mockResolvedValue({ plugins: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.updateSettings.mockResolvedValue({ config, overrides: {} });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SettingsRoute open-question answers", () => {
  it("shows the questions the user saved and hides cleared ones", async () => {
    prime({ "Why us?": "Because I care about the product.", "Old question?": "" });
    render(<SettingsRoute />);

    expect(await screen.findByDisplayValue("Why us?")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Because I care about the product.")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Old question?")).not.toBeInTheDocument();
  });

  it("saves a new question and clears one the user removed", async () => {
    prime({ "Old question?": "Old answer." });
    render(<SettingsRoute />);

    await screen.findByDisplayValue("Old question?");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a question" }));
    fireEvent.change(screen.getByLabelText("Question 1"), { target: { value: "Why us?" } });
    fireEvent.change(screen.getByLabelText("Answer 1"), { target: { value: "Because." } });
    fireEvent.click(screen.getByRole("button", { name: "Save open-question answers" }));

    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledTimes(1));
    expect(mocks.updateSettings).toHaveBeenCalledWith({
      patch: { autofill: { snippets: { "Why us?": "Because.", "Old question?": "" } } },
    });
  });
});
