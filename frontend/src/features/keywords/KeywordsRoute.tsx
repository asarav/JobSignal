import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  CompaniesListResponseCompanyInfo,
  KeywordsCoverageResponse,
  KeywordsPrevalenceResponse,
  KeywordsTrendsResponse,
} from "../../types/generated";

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

type Row = Record<string, unknown>;

function keywordId(row: Row): string {
  return String(row.keyword_id ?? "");
}

function share(row: Row, insufficient: boolean): string {
  if (insufficient || row.prevalence === null || row.prevalence === undefined) return "—";
  const value = Number(row.prevalence);
  return `${(value * 100).toFixed(0)}%`;
}

export function KeywordsRoute() {
  const [companies, setCompanies] = useState<CompaniesListResponseCompanyInfo[]>([]);
  const [companyId, setCompanyId] = useState<string>("");
  const [prevalence, setPrevalence] = useState<KeywordsPrevalenceResponse | null>(null);
  const [coverage, setCoverage] = useState<KeywordsCoverageResponse | null>(null);
  const [trends, setTrends] = useState<KeywordsTrendsResponse | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const scope = companyId ? { company_id: companyId } : {};
      const [next, nextCoverage, nextTrends] = await Promise.all([
        workerClient.getKeywordsPrevalence(scope),
        workerClient.getKeywordsCoverage(scope),
        workerClient.getKeywordTrends({ ...scope, periods: 12 }),
      ]);
      setPrevalence(next);
      setCoverage(nextCoverage);
      setTrends(nextTrends);
      setError(null);
    } catch (exc) {
      setError(message(exc));
    }
  }, [companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    workerClient
      .listCompanies()
      .then((result) => setCompanies(result.companies ?? []))
      .catch(() => setCompanies([]));
  }, []);

  const backfill = async () => {
    try {
      const result = await workerClient.backfillKeywords({ limit: 200 });
      setStatus(
        `Read ${result.processed ?? 0} stored postings; ${result.remaining ?? 0} still to read.`,
      );
      await load();
    } catch (exc) {
      setError(message(exc));
    }
  };

  const small = prevalence?.small_sample ?? true;
  const rows = (prevalence?.keywords ?? []) as Row[];
  const periods = (trends?.periods ?? []) as Row[];
  const trendKeywords = (trends?.keywords ?? []) as Row[];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Keyword Insights"
        description="Skill demand measured from the postings this computer has collected. Counts are shown as observations, never as a forecast."
      />
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <details className="rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-sm">
        <summary className="cursor-pointer font-semibold text-slate-900">
          How this works
        </summary>
        <div className="mt-2 space-y-2 text-slate-600">
          <p>
            Each posting is read once per revision and its recognised skills are stored with that
            revision. The pipeline does this while it assesses a posting, so the table below fills
            without pressing anything.
          </p>
          <p>
            Prevalence is the share of postings in scope that mention a skill. Trends group the
            same history by the month a posting revision was read, so you can see counts move
            without treating a small month as a trend. Coverage compares those skills with the
            skills on your profile.
          </p>
          <p>
            A posting with no readable text counts as missing evidence, not as a posting without
            the skill. Fewer than {prevalence?.min_sample ?? 10} postings in a period is too small
            a sample for a rate, so that period shows counts only.
          </p>
        </div>
      </details>

      <section className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <label className="text-sm font-medium text-slate-700">
          Scope
          <select
            className="mt-1 block rounded border border-slate-300 px-2 py-1 text-sm"
            value={companyId}
            onChange={(event) => setCompanyId(event.target.value)}
          >
            <option value="">All companies</option>
            {companies.map((company) => (
              <option key={company.company_id} value={company.company_id}>
                {company.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
          onClick={backfill}
        >
          Read stored postings
        </button>
        {prevalence ? (
          <span className="text-xs text-slate-500">
            {prevalence.n_jobs === null || prevalence.n_jobs === undefined
              ? "scope size not reported"
              : `${prevalence.n_jobs} postings in scope`}
            {small ? " · too small a sample for a rate" : ""}
          </span>
        ) : null}
      </section>

      {status ? <p className="text-xs text-slate-500">{status}</p> : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Skill prevalence</h2>
        {rows.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No keywords stored yet. Crawl a source or read stored postings above.
          </p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-500">
                <th className="py-1">Skill</th>
                <th>Postings</th>
                <th>Share</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={keywordId(row)} className="border-t border-slate-100">
                  <td className="py-2 font-medium text-slate-900">{keywordId(row)}</td>
                  <td>{row.jobs === null || row.jobs === undefined ? "—" : String(row.jobs)}</td>
                  <td>{share(row, Boolean(row.insufficient ?? small))}</td>
                  <td className="text-xs text-slate-400">
                    {row.insufficient || small ? "sample too small" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {prevalence?.note ? (
          <p className="mt-2 text-xs text-slate-500">{prevalence.note}</p>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Skill trends by month</h2>
        <p className="mt-1 text-xs text-slate-500">
          Counts per month from the stored posting revisions. A month below the sample threshold
          keeps its counts but shows no share; a month with no postings says so instead of
          plotting zero.
        </p>
        {periods.length === 0 || trendKeywords.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No trend data yet. Postings appear here once a revision has been read.
          </p>
        ) : (
          <>
            <div className="mt-3 flex flex-wrap gap-4">
              {trendKeywords.map((keyword) => {
                const counts = periods.map((period) => {
                  const values = (period.keywords ?? []) as Row[];
                  const match = values.find(
                    (value) => keywordId(value) === keywordId(keyword),
                  );
                  return {
                    period: String(period.period),
                    jobs: Number(match?.jobs ?? 0),
                    noData: Boolean(period.no_data),
                    insufficient: Boolean(period.insufficient),
                  };
                });
                const peak = Math.max(1, ...counts.map((entry) => entry.jobs));
                return (
                  <figure key={keywordId(keyword)} className="w-40">
                    <figcaption className="text-xs font-medium text-slate-700">
                      {keywordId(keyword)}
                    </figcaption>
                    <div className="mt-1 flex h-12 items-end gap-0.5" aria-hidden="true">
                      {counts.map((entry) => (
                        <div
                          key={entry.period}
                          title={`${entry.period}: ${entry.jobs} postings${entry.insufficient ? " (sample too small)" : ""}`}
                          className={`w-2 rounded-t ${
                            entry.noData
                              ? "h-px bg-slate-300"
                              : entry.insufficient
                                ? "bg-slate-400"
                                : "bg-slate-800"
                          }`}
                          style={
                            entry.noData
                              ? undefined
                              : { height: `${Math.max(4, (entry.jobs / peak) * 100)}%` }
                          }
                        />
                      ))}
                    </div>
                    <p className="mt-1 text-[10px] text-slate-400">
                      postings per month · grey = sample too small · blank = no postings
                    </p>
                  </figure>
                );
              })}
            </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-slate-500">
                  <th className="py-1">Month</th>
                  <th>Postings</th>
                  {trendKeywords.map((row) => (
                    <th key={keywordId(row)}>{keywordId(row)}</th>
                  ))}
                  <th>Sample</th>
                </tr>
              </thead>
              <tbody>
                {periods.map((period) => {
                  const values = (period.keywords ?? []) as Row[];
                  const insufficient = Boolean(period.insufficient);
                  return (
                    <tr key={String(period.period)} className="border-t border-slate-100">
                      <td className="py-2 font-medium text-slate-900">
                        {String(period.period)}
                      </td>
                      <td>
                        {period.n_jobs === null || period.n_jobs === undefined
                          ? "—"
                          : String(period.n_jobs)}
                      </td>
                      {values.map((value) => (
                        <td key={keywordId(value)}>
                          {value.jobs === null || value.jobs === undefined
                            ? "—"
                            : String(value.jobs)}
                          {value.prevalence === null || value.prevalence === undefined ? (
                            <span className="ml-1 text-xs text-slate-400">count only</span>
                          ) : (
                            <span className="ml-1 text-xs text-slate-500">
                              {String(value.prevalence)}
                            </span>
                          )}
                        </td>
                      ))}
                      <td className="text-xs text-slate-400">
                        {period.no_data
                          ? "no postings"
                          : insufficient
                            ? "insufficient data"
                            : "ok"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
        {trends?.note ? <p className="mt-2 text-xs text-slate-500">{trends.note}</p> : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Coverage of your profile</h2>
        {coverage && (coverage.candidate_skills ?? 0) > 0 ? (
          <>
            <p className="mt-1 text-sm text-slate-600">
              {coverage.coverage_ratio === null || coverage.coverage_ratio === undefined
                ? "Not enough data for a ratio."
                : `${Math.round((coverage.coverage_ratio ?? 0) * 100)}% of the skills asked for most are on your profile.`}
            </p>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-slate-700">On your profile</h3>
                <ul className="mt-1 text-sm text-slate-700">
                  {(coverage.covered ?? []).map((row, index) => (
                    <li key={index}>{keywordId(row as Row)}</li>
                  ))}
                  {(coverage.covered ?? []).length === 0 ? <li>None yet.</li> : null}
                </ul>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-700">Asked for, not on your profile</h3>
                <ul className="mt-1 text-sm text-slate-700">
                  {(coverage.missing ?? []).map((row, index) => (
                    <li key={index}>{keywordId(row as Row)}</li>
                  ))}
                  {(coverage.missing ?? []).length === 0 ? <li>Nothing missing.</li> : null}
                </ul>
              </div>
            </div>
          </>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            Import a resume with skills to compare them with the postings in scope.
          </p>
        )}
        {coverage?.note ? <p className="mt-2 text-xs text-slate-500">{coverage.note}</p> : null}
      </section>
    </div>
  );
}
