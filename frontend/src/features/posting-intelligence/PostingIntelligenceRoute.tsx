import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  MatchingGetWeightHistoryResponse,
  MatchingGetWeightProposalsResponse,
} from "../../types/generated";

type Tab = "model" | "companies" | "calibration" | "weights" | "how";

const TABS: { id: Tab; label: string }[] = [
  { id: "model", label: "Model" },
  { id: "companies", label: "Company evidence" },
  { id: "calibration", label: "Calibration" },
  { id: "weights", label: "Weight suggestions" },
  { id: "how", label: "How this works" },
];

function asText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function PostingIntelligenceRoute() {
  const [tab, setTab] = useState<Tab>("model");
  const [config, setConfig] = useState(
    JSON.stringify({ prior_risk: 0.2, prior_weight: 0.15 }, null, 2),
  );
  const [modelResult, setModelResult] = useState<Record<string, unknown> | null>(null);
  const [runResult, setRunResult] = useState<Record<string, unknown> | null>(null);
  const [calibration, setCalibration] = useState<Record<string, unknown> | null>(null);
  const [proposals, setProposals] = useState<MatchingGetWeightProposalsResponse | null>(null);
  const [weightHistory, setWeightHistory] =
    useState<MatchingGetWeightHistoryResponse | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [weightNotice, setWeightNotice] = useState<string | null>(null);
  const [companyId, setCompanyId] = useState("");
  const [companyResult, setCompanyResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveModel = () =>
    run(async () => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(config) as Record<string, unknown>;
      } catch {
        throw new WorkerError("validation.invalid_params", "Config must be valid JSON.");
      }
      const result = await workerClient.configurePostingIntelligence({ config: parsed });
      setModelResult(result as unknown as Record<string, unknown>);
    });

  const evaluate = () =>
    run(async () => {
      const result = await workerClient.evaluatePostingIntelligence({
        scope: "active",
        limit: 200,
      });
      setRunResult(result as unknown as Record<string, unknown>);
    });

  const loadCalibration = () =>
    run(async () => {
      const result = await workerClient.getCalibration();
      setCalibration(result as unknown as Record<string, unknown>);
    });

  const loadWeights = useCallback(
    () =>
      run(async () => {
        const [nextProposals, nextHistory] = await Promise.all([
          workerClient.getWeightProposals(),
          workerClient.getWeightHistory(),
        ]);
        setProposals(nextProposals);
        setWeightHistory(nextHistory);
      }),
    [],
  );

  useEffect(() => {
    if (tab === "weights" && !proposals) void loadWeights();
  }, [tab, proposals, loadWeights]);

  const acceptProposal = () =>
    run(async () => {
      const proposal = (proposals?.proposal ?? null) as Record<string, unknown> | null;
      if (!proposal) return;
      await workerClient.applyWeightProposal({
        weights: proposal.weights as Record<string, number>,
        evidence: {
          sample_size: proposal.sample_size,
          gap: proposal.gap,
          dimension: proposal.dimension,
        },
        reason: "accepted from weight suggestions",
      });
      setWeightNotice("New weighting saved. The previous weighting is kept in the history below.");
      setConfirming(false);
      await loadWeights();
    });

  const loadCompany = () =>
    run(async () => {
      if (!companyId.trim()) return;
      const result = await workerClient.getCompanyAssessment({ company_id: companyId.trim() });
      setCompanyResult(result as unknown as Record<string, unknown>);
    });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Posting intelligence"
        description="Why a posting may be stale or duplicated, with the evidence and its confidence kept separate from your fit score."
      />
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <div className="flex gap-1" role="tablist" aria-label="Posting intelligence views">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            role="tab"
            type="button"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === entry.id
                ? "bg-slate-900 text-white"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "model" ? (
        <div className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Model configuration</h2>
            <p className="mt-1 text-xs text-slate-500">
              Saving a change writes a new model version. Earlier assessments keep the
              version that produced them, so a verdict can always be re-derived.
            </p>
            <label className="mt-3 block text-xs font-medium text-slate-600" htmlFor="pi-config">
              Configuration (JSON: prior_risk, prior_weight, weights, thresholds)
            </label>
            <textarea
              id="pi-config"
              className="mt-1 h-40 w-full rounded border border-slate-300 p-2 font-mono text-xs"
              value={config}
              onChange={(event) => setConfig(event.target.value)}
            />
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void saveModel()}
                className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                Save model version
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void evaluate()}
                className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Evaluate active postings
              </button>
            </div>
            {modelResult ? (
              <p className="mt-3 text-sm text-slate-700">
                Version {asText(modelResult.model_version)} · config hash{" "}
                {asText(modelResult.config_hash)}
              </p>
            ) : null}
            {runResult ? (
              <p className="mt-2 text-sm text-slate-600">
                {asText(runResult.evaluated)} evaluated, {asText(runResult.skipped)} reused
                an identical input. {runResult.task_id ? "Running as a background task." : ""}
              </p>
            ) : null}
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">What the numbers mean</h2>
            <dl className="mt-2 grid gap-2 text-sm text-slate-700 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-slate-500">Risk</dt>
                <dd>How much the evidence suggests the posting may be inactive or duplicated.</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Confidence</dt>
                <dd>How much evidence was available to support that risk estimate.</dd>
              </div>
            </dl>
            <p className="mt-2 text-xs text-slate-500">
              They are reported separately. A high risk with low confidence is weak evidence,
              and a missing signal never raises risk.
            </p>
          </section>
        </div>
      ) : null}

      {tab === "companies" ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">Company hiring evidence</h2>
          <p className="mt-1 text-xs text-slate-500">
            Observed postings, reposts and confirmed applications for one company. A company
            with little history shows little history; it is not labelled as bad.
          </p>
          <div className="mt-3 flex gap-2">
            <input
              className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
              placeholder="Company id"
              aria-label="Company id"
              value={companyId}
              onChange={(event) => setCompanyId(event.target.value)}
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => void loadCompany()}
              className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              Look up
            </button>
          </div>
          {companyResult ? (
            <pre className="mt-3 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
              {asText(companyResult.summary)}
            </pre>
          ) : null}
        </section>
      ) : null}

      {tab === "calibration" ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-slate-900">Risk calibration</h2>
            <button
              type="button"
              disabled={busy}
              onClick={() => void loadCalibration()}
              className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Compute calibration
            </button>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Compares predicted risk with what endpoint checks observed. Samples below
            30 observations produce no proposal, and nothing changes unless you accept
            a new model version.
          </p>
          {calibration ? (
            <div className="mt-3 space-y-2 text-sm text-slate-700">
              <p>
                {String(calibration.sample_size)} observations ·{" "}
                {String(calibration.observed_closed)} observed closed · minimum{" "}
                {String(calibration.min_sample)}
              </p>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-slate-500">
                    <th className="py-1">Risk band</th>
                    <th>Predicted</th>
                    <th>Observed closed</th>
                    <th>n</th>
                    <th>Interval</th>
                  </tr>
                </thead>
                <tbody>
                  {((calibration.buckets ?? []) as Record<string, unknown>[]).map(
                    (bucket, index) => {
                      const insufficient = bucket.insufficient === true;
                      const interval = bucket.interval as number[] | null | undefined;
                      return (
                        <tr key={index} className="border-t border-slate-100">
                          <td className="py-1">{String(bucket.risk_band)}</td>
                          <td>{String(bucket.predicted)}</td>
                          <td>{insufficient ? "hidden" : String(bucket.rate)}</td>
                          <td>{String(bucket.n)}</td>
                          <td>
                            {insufficient || !interval
                              ? "—"
                              : `${interval[0].toFixed(2)}–${interval[1].toFixed(2)}`}
                          </td>
                        </tr>
                      );
                    },
                  )}
                </tbody>
              </table>
              {calibration.proposal ? (
                <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
                  Proposal only: a prior risk of{" "}
                  {String(
                    (calibration.proposal as Record<string, unknown>)
                      .suggested_prior_risk,
                  )}{" "}
                  from {String(calibration.sample_size)} observations. Saving it writes a new
                  model version; earlier assessments are kept.
                </div>
              ) : (
                <p className="text-xs text-slate-500">
                  {String(calibration.note ?? "Not enough observations for a proposal yet.")}
                </p>
              )}
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "weights" ? (
        <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-slate-900">Weight suggestions</h2>
            <button
              type="button"
              disabled={busy}
              onClick={() => void loadWeights()}
              className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Check my outcomes
            </button>
          </div>
          <p className="text-xs text-slate-500">
            Compares the fit dimensions stored with applications whose outcomes you
            recorded. These are associations in your own history, with the sample size
            shown. Nothing is applied until you accept a suggestion, and the previous
            weighting stays in the history.
          </p>
          {weightNotice ? (
            <p className="rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
              {weightNotice}
            </p>
          ) : null}
          {proposals ? (
            <div className="space-y-3 text-sm text-slate-700">
              <p>
                {String(proposals.applications ?? 0)} applications with a stored fit ·{" "}
                {String(proposals.responded ?? 0)} with a reply ·{" "}
                {String(proposals.not_responded ?? 0)} without. Minimum sample{" "}
                {String(proposals.min_sample ?? 0)} with {String(proposals.min_per_side ?? 0)}{" "}
                on each side.
              </p>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-slate-500">
                    <th className="py-1">Dimension</th>
                    <th>Mean with reply</th>
                    <th>Mean without</th>
                    <th>Gap</th>
                    <th>n</th>
                    <th>Sample</th>
                  </tr>
                </thead>
                <tbody>
                  {((proposals.associations ?? []) as Record<string, unknown>[]).map(
                    (row, index) => (
                      <tr key={index} className="border-t border-slate-100">
                        <td className="py-1">{String(row.dimension)}</td>
                        <td>{row.mean_responded === null ? "—" : String(row.mean_responded)}</td>
                        <td>{row.mean_not_responded === null ? "—" : String(row.mean_not_responded)}</td>
                        <td>{row.gap === null ? "—" : String(row.gap)}</td>
                        <td>{`${String(row.n_responded)} / ${String(row.n_not_responded)}`}</td>
                        <td className="text-slate-400">{row.insufficient ? "insufficient" : "ok"}</td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
              {proposals.insufficient_evidence ? (
                <p className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
                  Not enough recorded outcomes for a suggestion yet. Record outcomes on the
                  Applications screen; the table above shows what has been observed so far.
                </p>
              ) : (
                proposals.proposal ? (
                  <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                    <p className="font-medium">
                      Suggestion: move weight toward {String((proposals.proposal as Record<string, unknown>).dimension)}{" "}
                      (observed gap {String((proposals.proposal as Record<string, unknown>).gap)}) from{" "}
                      {String((proposals.proposal as Record<string, unknown>).sample_size)} applications.
                    </p>
                    <ul className="mt-1 list-disc pl-4">
                      {((((proposals.proposal as Record<string, unknown>).changes ?? []) as Record<string, unknown>[])).map(
                        (change, index) => (
                          <li key={index}>
                            {String(change.dimension)}: {Number(change.from).toFixed(2)} →{" "}
                            {Number(change.to).toFixed(2)}
                          </li>
                        ),
                      )}
                    </ul>
                    {confirming ? (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span>Apply this weighting? The previous one is kept.</span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void acceptProposal()}
                          className="rounded bg-amber-700 px-3 py-1 font-medium text-white"
                        >
                          Accept weighting
                        </button>
                        <button
                          type="button"
                          className="rounded border border-amber-400 px-3 py-1"
                          onClick={() => setConfirming(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setConfirming(true)}
                        className="mt-2 rounded bg-amber-700 px-3 py-1 font-medium text-white"
                      >
                        Review and accept
                      </button>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">{String(proposals.note ?? "")}</p>
                )
              )}
              <details className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                <summary className="cursor-pointer font-medium">Why this suggestion</summary>
                <p className="mt-1">
                  For each fit dimension, the stored score is averaged over applications that
                  got a reply and over those that did not. A dimension with a large observed
                  gap is suggested for a small weight increase. The numbers describe your own
                  history; they do not show that the dimension produced a reply, and they are
                  not a probability of getting an interview.
                </p>
              </details>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Load suggestions to see your recorded outcomes.</p>
          )}
          {weightHistory && (weightHistory.versions ?? []).length > 0 ? (
            <div>
              <h3 className="text-sm font-semibold text-slate-700">Weighting history</h3>
              <table className="mt-1 w-full text-left text-xs">
                <thead>
                  <tr className="text-slate-500">
                    <th className="py-1">When</th>
                    <th>Source</th>
                    <th>Weights</th>
                    <th>Replaced</th>
                  </tr>
                </thead>
                <tbody>
                  {((weightHistory.versions ?? []) as Record<string, unknown>[]).map(
                    (version, index) => (
                      <tr key={index} className="border-t border-slate-100">
                        <td className="py-1">{String(version.created_at)}</td>
                        <td>{String(version.source)}</td>
                        <td>{JSON.stringify(version.weights)}</td>
                        <td>
                          {version.previous_weights
                            ? JSON.stringify(version.previous_weights)
                            : "built-in default"}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "how" ? (
        <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">How posting intelligence works</h2>
          <p>
            Each posting is checked for signals such as age, repeated sightings, repost wording
            and the state of the employer&apos;s application page. Signals combine into a risk
            estimate and a separate confidence estimate.
          </p>
          <p>
            A single weak signal stays below the “needs verification” threshold. When no
            evidence exists, the verdict stays neutral rather than becoming a warning.
          </p>
          <p>
            The endpoint check fetches the page once, over plain HTTP, respecting robots rules
            and a per-posting budget. It never clicks a control, types into a form, or submits
            an application.
          </p>
          <p>
            Every change is stored as a new row with the model version that produced it, so a
            verdict can be re-derived later. See{" "}
            <code>docs/how-posting-intelligence-works.md</code> for thresholds and limits.
          </p>
        </section>
      ) : null}
    </div>
  );
}
