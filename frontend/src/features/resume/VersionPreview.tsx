import { useState } from "react";
import { canOpenOriginal, openOriginalFile } from "../../services/openOriginal";
import { diffLines, summarizeDiff } from "./lineDiff";
import { parsedText, versionLabel, type ResumeVersionRow } from "./resumeVersions";

interface VersionPreviewProps {
  version: ResumeVersionRow;
  previous: ResumeVersionRow | null;
}

const kindStyles: Record<string, string> = {
  added: "bg-emerald-50 text-emerald-800",
  removed: "bg-red-50 text-red-800",
  equal: "text-slate-600",
};

const kindMarkers: Record<string, string> = {
  added: "+",
  removed: "−",
  equal: " ",
};

/** Read-only parsed text of one version, highlighted against the one before it. */
export function VersionPreview({ version, previous }: VersionPreviewProps) {
  const [openError, setOpenError] = useState<string | null>(null);
  const storedPath = version.stored_path ?? null;
  const canOpen = storedPath !== null && canOpenOriginal();
  const text = parsedText(version);
  const previousText = previous ? parsedText(previous) : null;
  const comparable = previous !== null && previousText !== null && text !== null;
  const lines = comparable ? diffLines(previousText, text) : diffLines("", text ?? "");
  const summary = summarizeDiff(lines);

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-600">
          {comparable
            ? `Compared with “${versionLabel(previous)}” (imported before this one)`
            : previous
              ? "The previous version has no stored text to compare with."
              : "First version — no earlier version to compare."}
        </p>
        <div className="flex items-center gap-3">
          {canOpen ? (
            <button
              type="button"
              title="Open the stored resume file in your computer's default app."
              onClick={() => {
                if (!storedPath) return;
                setOpenError(null);
                void openOriginalFile(storedPath).catch((error: unknown) => {
                  setOpenError(error instanceof Error ? error.message : String(error));
                });
              }}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Open original
            </button>
          ) : null}
          {comparable ? (
            <p className="flex items-center gap-3 text-xs font-medium">
              <span className="text-emerald-700">+{summary.added} added</span>
              <span className="text-red-700">−{summary.removed} removed</span>
            </p>
          ) : null}
        </div>
      </div>

      {openError ? (
        <p className="mt-2 text-xs text-red-700">Could not open the file: {openError}</p>
      ) : null}

      {text === null ? (
        <p className="mt-2 text-sm text-slate-500">
          No parsed text is stored for this version, so it cannot be previewed or compared.
        </p>
      ) : (
        <div className="mt-2 max-h-[28rem] overflow-auto rounded border border-slate-200 bg-white py-1">
          {lines.map((line, index) => (
            <div
              key={`${index}-${line.kind}`}
              className={`flex gap-2 px-2 py-0.5 font-mono text-xs leading-5 ${kindStyles[line.kind]}`}
            >
              <span aria-hidden="true" className="w-3 shrink-0 select-none text-slate-400">
                {kindMarkers[line.kind]}
              </span>
              <span className="whitespace-pre-wrap break-words">{line.text || "\u00a0"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
