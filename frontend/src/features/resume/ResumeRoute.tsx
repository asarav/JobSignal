import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  ResumeImportResponse,
} from "../../types/generated";

export function ResumeRoute() {
  const [filePath, setFilePath] = useState("");
  const [label, setLabel] = useState("Imported");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importResult, setImportResult] = useState<ResumeImportResponse | null>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [matchResults, setMatchResults] = useState<any[]>([]);

  const loadVersions = useCallback(async () => {
    try {
      const r = await workerClient.listResumeVersions({ profile_id: "default" });
      setVersions(r.versions ?? []);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  const doImport = async () => {
    if (!filePath.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await workerClient.importResume({ file_path: filePath.trim(), label });
      setImportResult(r);
      setFilePath("");
      await loadVersions();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const runMatching = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await workerClient.runMatching({ profile_id: "default", job_ids: [] });
      setMatchResults(r.results ?? []);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Resume" description="Import a resume and match it to jobs." />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Import resume</h2>
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="/path/to/resume.txt (or .pdf / .docx)"
            value={filePath}
            onChange={(e) => setFilePath(e.target.value)}
          />
          <input
            className="w-32 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void doImport()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Importing..." : "Import"}
          </button>
        </div>
        {importResult ? (
          <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            Imported {importResult.version_id}. Skills: {(importResult.skills ?? []).join(", ") || "(none)"}.
            Sections: {(importResult.sections ?? []).join(", ") || "(none)"}.
          </div>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Versions ({versions.length})</h2>
        <ul className="mt-3 divide-y divide-slate-100">
          {versions.map((v) => (
            <li key={v.id} className="py-2 text-sm">
              <div className="font-medium text-slate-900">{v.label}</div>
              <div className="text-xs text-slate-500">
                {v.parser_id} · {v.is_active ? "active" : "inactive"}
              </div>
            </li>
          ))}
          {versions.length === 0 ? <li className="text-sm text-slate-500">No versions yet.</li> : null}
        </ul>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Matching</h2>
        <button
          type="button"
          onClick={() => void runMatching()}
          disabled={busy}
          className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "Running..." : "Run matching"}
        </button>
        {matchResults.length > 0 ? (
          <ul className="mt-3 divide-y divide-slate-100">
            {matchResults.map((r) => (
              <li key={r.result_id} className="py-2 text-sm">
                <div className="font-medium text-slate-900">Job {r.job_id}</div>
                <div className="text-xs text-slate-500">
                  fit: {Number(r.fit_score).toFixed(2)} · coverage: {Number(r.dimension_coverage).toFixed(2)}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
