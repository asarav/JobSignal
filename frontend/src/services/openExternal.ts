import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./workerBridge";

/** Only ordinary web pages can be opened; anything else is refused. */
export function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Opens a posting in the user's default browser.
 *
 * In the desktop shell this goes through a Rust command that only accepts
 * http(s); in the browser preview it opens a new tab. Nothing is clicked,
 * typed or submitted.
 */
export async function openExternalUrl(url: string): Promise<void> {
  if (!isHttpUrl(url)) return;
  if (isTauriRuntime()) {
    await invoke("open_external_url", { url });
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
