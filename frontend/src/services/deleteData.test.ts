import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invoke(...args),
}));

const isTauriRuntime = vi.fn();
vi.mock("./workerBridge", () => ({
  isTauriRuntime: () => isTauriRuntime(),
}));

import { deleteAllData } from "./deleteData";

describe("deleteAllData", () => {
  beforeEach(() => {
    invoke.mockReset();
    isTauriRuntime.mockReset();
  });

  it("requires the exact typed confirmation", async () => {
    isTauriRuntime.mockReturnValue(true);
    for (const value of ["delete", "Delete", "DELETE!", "yes", ""]) {
      await expect(deleteAllData(value)).rejects.toThrow(/type delete/i);
    }
    expect(invoke).not.toHaveBeenCalled();
  });

  it("refuses outside the desktop app with an actionable error", async () => {
    isTauriRuntime.mockReturnValue(false);
    await expect(deleteAllData("DELETE")).rejects.toThrow(/desktop app/i);
    expect(invoke).not.toHaveBeenCalled();
  });

  it("invokes the supervisor only after confirmation", async () => {
    isTauriRuntime.mockReturnValue(true);
    invoke.mockResolvedValue({ ok: true });
    await deleteAllData("DELETE");
    expect(invoke).toHaveBeenCalledWith("delete_all_data", { confirm: "DELETE" });
  });
});
