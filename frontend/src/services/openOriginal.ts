import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./workerBridge";

/** Opening a stored copy needs the native shell; the preview has no files. */
export function canOpenOriginal(): boolean {
  return isTauriRuntime();
}

/**
 * Opens the imported copy of a resume in the OS default application. The Rust
 * side refuses any path outside the worker data dir.
 */
export async function openOriginalFile(path: string): Promise<void> {
  if (!isTauriRuntime()) return;
  await invoke("open_original_file", { path });
}
