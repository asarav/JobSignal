import { useEffect, useState } from "react";
import { workerClient, WorkerError } from "../../services/workerClient";

interface Dimension {
  dimension?: string;
  score?: number;
  weight?: number;
  explanation?: string;
}

function readDimensions(row: Record<string, unknown>): Dimension[] {
  const raw = row.dimensions;
  return Array.isArray(raw) ? (raw as Dimension[]) : [];
}

export function FitPanel({
  jobId,
  storedFit,
}: {
  jobId: string;
  storedFit?: number | null;
}) {
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await workerClient.getMatchingResults();
        const match = (response.results ?? []).find((row) => row.job_id === jobId);
        if (!cancelled) setResult((match as Record<string, unknown>) ?? null);
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const dimensions = result ? readDimensions(result) : [];
  const fit = typeof result?.fit_score === "number" ? (result.fit_score as number) : null;

  return (
    <section
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      aria-label="Fit breakdown"
    >
      <h2 className="text-lg font-semibold text-slate-900">Fit breakdown</h2>
      <p className="mt-1 text-xs text-slate-500">
        Satisfaction only, from your profile weights. Credibility is shown separately in
        posting risk and never changes this number.
      </p>
      {error ? <p className="mt-2 text-sm text-red-700">{error}</p> : null}
      {!result ? (
        typeof storedFit === "number" ? (
          <p className="mt-2 text-sm text-slate-700">
            Stored fit {storedFit.toFixed(2)}. Recompute matches from the resume
            screen to see the dimension breakdown.
          </p>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            No fit score yet for this job. Compute fit from Discover or the resume
            screen.
          </p>
        )
      ) : (
        <>
          <p className="mt-2 text-sm text-slate-700">
            Fit {fit === null ? "—" : fit.toFixed(2)}
            {typeof result.coverage === "number"
              ? ` · evidence coverage ${(result.coverage as number).toFixed(2)}`
              : ""}
          </p>
          {dimensions.length > 0 ? (
            <ul className="mt-2 space-y-1 text-sm text-slate-700">
              {dimensions.map((dimension, index) => (
                <li key={index} className="flex items-center gap-2">
                  <span className="w-40 shrink-0 capitalize">
                    {String(dimension.dimension ?? "dimension").replaceAll("_", " ")}
                  </span>
                  <span className="font-medium">
                    {typeof dimension.score === "number"
                      ? dimension.score.toFixed(2)
                      : "—"}
                  </span>
                  {typeof dimension.weight === "number" ? (
                    <span className="text-xs text-slate-500">
                      weight {dimension.weight.toFixed(2)}
                    </span>
                  ) : null}
                  {dimension.explanation ? (
                    <span className="text-xs text-slate-500">{dimension.explanation}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-slate-500">
              Dimension detail is not stored for this result; the fit score is still valid.
            </p>
          )}
        </>
      )}
    </section>
  );
}
