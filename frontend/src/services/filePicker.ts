import { isTauriRuntime } from "./workerBridge";

/**
 * Preview stand-in so the import flow stays reviewable without the Tauri
 * shell. The preview banner already states that values are placeholders.
 */
const PREVIEW_PATH = "C:\\Users\\You\\Documents\\resume.pdf";

/**
 * Opens the OS file dialog and returns the selected path, or `null` when the
 * user cancels. The worker only accepts a real filesystem path, which is why
 * the dialog lives in the native layer rather than an `<input type="file">`.
 */
export async function pickResumeFile(): Promise<string | null> {
  if (!isTauriRuntime()) {
    return PREVIEW_PATH;
  }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    multiple: false,
    directory: false,
    title: "Choose a resume",
    filters: [{ name: "Resume", extensions: ["pdf", "docx", "txt", "md"] }],
  });
  return typeof selected === "string" ? selected : null;
}
