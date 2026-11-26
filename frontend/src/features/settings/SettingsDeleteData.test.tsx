import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsRoute } from "./SettingsRoute";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listPlugins: vi.fn(),
  listSources: vi.fn(),
  getBackupHistory: vi.fn(),
  deleteAllData: vi.fn(),
}));

vi.mock("../../services/workerClient", () => ({
  workerClient: mocks,
  WorkerError: class WorkerError extends Error {
    readonly code: string;
    readonly retryable = false;
    constructor(code: string, message: string) {
      super(message);
      this.code = code;
    }
  },
}));

vi.mock("../../services/deleteData", () => ({
  deleteAllData: (confirm: string) => mocks.deleteAllData(confirm),
}));

beforeEach(() => {
  vi.clearAllMocks();
  // shouldAdvanceTime keeps Testing Library's async helpers working while letting
  // us drop the component's post-delete reload timer before it fires.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mocks.getSettings.mockResolvedValue({ config: {}, overrides: {}, schema_version: 1 });
  mocks.listPlugins.mockResolvedValue({ plugins: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.getBackupHistory.mockResolvedValue({ runs: [] });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

async function renderSettings() {
  render(
    <MemoryRouter initialEntries={["/settings?tab=data"]}>
      <SettingsRoute />
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: /backup and restore/i });
}

function deleteButton() {
  return screen.getByRole("button", { name: "Delete all local data" });
}

describe("SettingsRoute delete all local data", () => {
  it("stays disabled until DELETE is typed exactly, then asks the supervisor", async () => {
    mocks.deleteAllData.mockResolvedValue(undefined);

    await renderSettings();
    const button = deleteButton();
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(mocks.deleteAllData).not.toHaveBeenCalled();

    const input = screen.getByLabelText(/type delete to confirm/i);
    for (const value of ["delete", "Delete", "DELETE!", "yes"]) {
      fireEvent.change(input, { target: { value } });
      expect(deleteButton()).toBeDisabled();
      fireEvent.click(deleteButton());
    }
    expect(mocks.deleteAllData).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "DELETE" } });
    expect(deleteButton()).toBeEnabled();
    fireEvent.click(deleteButton());

    await waitFor(() => expect(mocks.deleteAllData).toHaveBeenCalledWith("DELETE"));
    expect(
      await screen.findByText(/Local data deleted\. The worker is restarting/i),
    ).toBeInTheDocument();
    // The confirmation is cleared so a second click needs a fresh DELETE.
    expect(input).toHaveValue("");
    expect(deleteButton()).toBeDisabled();
  });

  it("shows the refusal and no success text when deletion is unavailable", async () => {
    mocks.deleteAllData.mockRejectedValue(
      new Error(
        "Deleting local data is only available in the desktop app. Nothing was deleted.",
      ),
    );

    await renderSettings();
    fireEvent.change(screen.getByLabelText(/type delete to confirm/i), {
      target: { value: "DELETE" },
    });
    fireEvent.click(deleteButton());

    expect(await screen.findByText(/only available in the desktop app/i)).toBeInTheDocument();
    expect(screen.queryByText(/Local data deleted/i)).not.toBeInTheDocument();
  });
});
