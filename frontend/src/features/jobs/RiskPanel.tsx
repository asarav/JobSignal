import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RiskBadge } from "../../components/RiskBadge";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { RiskPanelSignal } from "./riskPanelTypes";

interface Props {
  jobId: string;
}

export function RiskPanel({ jobId }: Props) {
  const [assessment, setAssessment] = useState<Record<string, unknown> | null>(null);
  const [signals, setSignals] = useState<RiskPanelSignal[]>([]);
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [signalsResult, historyResult] = await Promise.all([
        workerClient.getPostingSignals({ job_id: jobId }),
        workerClient.getRiskHistory({ job_id: jobId }),
      ]);
      setAssessment(signalsResult.assessment ?? null);
      setSignals((signalsResult.signals ?? []) as RiskPanelSignal[]);
      setHistory((historyResult.changes ?? []) as Record<string, unknown>[]);
      setError(null);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  const verifyNow = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await workerClient.verifyJobNow({ job_id: jobId });
      await load();
      const checked = result.checked ?? 0;
      setNotice(
        checked > 0
          ? "Checked the application page without clicking anything. Evidence is recorded below."
          : "This posting was checked recently, so the polite budget skipped it.",
      );
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const riskClass = typeof assessment?.class === "string" ? assessment.class : null;
  const confidence =
    typeof assessment?.confidence === "number" ? (assessment.confidence as number) : null;
  const risk = typeof assessment?.risk === "number" ? (assessment.risk as number) : null;

  return (
    <section
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      aria-label="Posting intelligence"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-900">
          Posting risk
          <span className="ml-2 align-middle">
            <RiskBadge riskClass={riskClass} confidence={confidence} />
          </span>
        </h2>
        <button
          type="button"
          onClick={() => void verifyNow()}
          disabled={busy}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          title="Fetch the employer's application page once, politely, without submitting anything."
        >
          {busy ? "Checking…" : "Verify now"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        A posting-quality signal, never a prediction about you. Missing evidence is not a
        negative signal. The check only fetches the page; it never clicks or submits.
      </p>

      {error ? (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">
          {error}
        </div>
      ) : null}
      {notice ? <p className="mt-2 text-sm text-slate-600">{notice}</p> : null}

      {assessment ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm text-slate-700 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Risk</dt>
            <dd>{risk === null ? "—" : risk.toFixed(2)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Confidence</dt>
            <dd>{confidence === null ? "—" : confidence.toFixed(2)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Evidence</dt>
            <dd>{String(assessment.evidence_sufficiency ?? "insufficient")}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Assessed</dt>
            <dd>{String(assessment.evaluated_at ?? "—")}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-2 text-sm text-slate-500">
          Not assessed yet. Evaluation runs when a posting is ingested or on the daily pass.
        </p>
      )}

      {signals.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm text-slate-700" aria-label="Signal evidence">
          {signals.map((signal, index) => (
            <li key={`${signal.signal_id ?? "signal"}-${index}`} className="flex flex-wrap gap-2">
              <span className="font-medium">{signal.signal_id ?? "signal"}</span>
              <span className="text-slate-500">{signal.status ?? "unknown"}</span>
              {typeof signal.risk === "number" ? (
                <span className="text-slate-500">risk {signal.risk.toFixed(2)}</span>
              ) : null}
              {signal.missing_reason ? (
                <span className="text-slate-400">{signal.missing_reason}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {history.length > 0 ? (
        <div className="mt-3 text-sm text-slate-700">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Classification history
          </h3>
          <ul className="mt-1 space-y-1">
            {history.map((change, index) => (
              <li key={index}>
                {String(change.from_class ?? "none")} → {String(change.to_class ?? "—")} ·{" "}
                {String(change.changed_at ?? "")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="mt-3 text-xs text-slate-500">
        <Link className="underline" to="/posting-intelligence">
          How posting intelligence works
        </Link>
      </p>
    </section>
  );
}
