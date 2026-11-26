import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./workerBridge";

/**
 * Ask the desktop supervisor to delete every local file (ADR-0017).
 * The confirmation is exact; preview builds return an actionable error.
 */
export async function deleteAllData(confirm: string): Promise<void> {
  if (confirm.trim() !== "DELETE") {
    throw new Error('Type DELETE exactly to confirm deleting all local data.');
  }
  if (!isTauriRuntime()) {
    throw new Error(
      "Deleting local data is only available in the desktop app. Nothing was deleted.",
    );
  }
  await invoke("delete_all_data", { confirm });
}
