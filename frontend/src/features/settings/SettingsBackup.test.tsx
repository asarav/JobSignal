import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsRoute } from "./SettingsRoute";
import { WorkerError } from "../../services/workerClient";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listPlugins: vi.fn(),
  listSources: vi.fn(),
  getBackupHistory: vi.fn(),
  exportBackup: vi.fn(),
  importBackup: vi.fn(),
  updateSettings: vi.fn(),
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

const runs: Array<Record<string, unknown>> = [];

beforeEach(() => {
  vi.clearAllMocks();
  runs.length = 0;
  mocks.getSettings.mockResolvedValue({ config: {}, overrides: {}, schema_version: 1 });
  mocks.listPlugins.mockResolvedValue({ plugins: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.getBackupHistory.mockImplementation(async () => ({ runs: [...runs] }));
});

async function renderSettings() {
  render(<SettingsRoute />);
  await screen.findByRole("heading", { name: /backup and restore/i });
}

describe("SettingsRoute backup and restore", () => {
  it("exports a backup and shows the path it wrote", async () => {
    mocks.exportBackup.mockImplementation(async () => {
      const run = {
        id: "br_1",
        kind: "export",
        path: "backups/jobsignal-backup-20260101T000000Z.json",
        created_at: "2026-01-01T00:00:00Z",
      };
      runs.push(run);
      return {
        ok: true,
        path: run.path,
        checksum: "0123456789abcdef",
        counts: { companies: 1 },
        schema_version: 18,
        created_at: run.created_at,
      };
    });

    await renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Export a backup" }));

    expect(
      await screen.findByText(/^Saved backups\/jobsignal-backup-20260101T000000Z\.json/),
    ).toBeInTheDocument();
    expect(mocks.exportBackup).toHaveBeenCalledWith({});
    await waitFor(() =>
      expect(
        screen.getByText(/export · 2026-01-01T00:00:00Z/),
      ).toBeInTheDocument(),
    );
  });

  it("refuses to import until the confirmation is ticked and sends confirm to the worker", async () => {
    mocks.importBackup.mockResolvedValue({
      ok: true,
      verified: true,
      path: "backups/a.json",
      checksum: "abc",
      counts: { companies: 1, sources: 2 },
      schema_version: 18,
      imported_at: "2026-03-01T00:00:00Z",
    });

    await renderSettings();
    const importButton = screen.getByRole("button", { name: "Restore from a backup" });
    expect(importButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Backup file/), {
      target: { value: "backups/a.json" },
    });
    expect(importButton).toBeDisabled();
    fireEvent.click(importButton);
    expect(mocks.importBackup).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(importButton).toBeEnabled();
    fireEvent.click(importButton);

    await waitFor(() =>
      expect(mocks.importBackup).toHaveBeenCalledWith({
        path: "backups/a.json",
        confirm: true,
      }),
    );
    expect(await screen.findByText(/Restored 2 tables \(3 rows\)/)).toBeInTheDocument();
  });

  it("surfaces a non-empty database refusal instead of reporting success", async () => {
    mocks.importBackup.mockRejectedValue(
      new WorkerError("conflict.backup_not_empty", "backup target already holds data"),
    );

    await renderSettings();
    fireEvent.change(screen.getByLabelText(/Backup file/), {
      target: { value: "backups/a.json" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Restore from a backup" }));

    const message = await screen.findByText(/conflict\.backup_not_empty/);
    expect(message).toBeInTheDocument();
    expect(screen.queryByText(/Restored/)).not.toBeInTheDocument();
  });

  it("renders the backup history returned by the worker", async () => {
    runs.push(
      {
        id: "br_1",
        kind: "export",
        path: "backups/one.json",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "br_2",
        kind: "import",
        path: "backups/two.json",
        created_at: "2026-02-01T00:00:00Z",
      },
    );

    await renderSettings();

    expect(await screen.findByText(/export · 2026-01-01T00:00:00Z · backups\/one\.json/)).toBeInTheDocument();
    expect(screen.getByText(/import · 2026-02-01T00:00:00Z · backups\/two\.json/)).toBeInTheDocument();
    expect(mocks.getBackupHistory).toHaveBeenCalledWith({ limit: 10 });
  });
});
