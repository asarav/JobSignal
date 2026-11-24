import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../../components/PageHeader";
import { RiskBadge } from "../../components/RiskBadge";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  CompaniesListResponseCompanyInfo,
  CompaniesGetDetailResponse,
  CompaniesGetMergeHistoryResponse,
} from "../../types/generated";

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

type Pending =
  | { kind: "merge"; mergedId: string; survivorId: string; name: string }
  | { kind: "unmerge"; mergedId: string; survivorId: string; name: string };

export function CompaniesRoute() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [companies, setCompanies] = useState<CompaniesListResponseCompanyInfo[]>([]);
  const [detail, setDetail] = useState<CompaniesGetDetailResponse | null>(null);
  const [history, setHistory] = useState<CompaniesGetMergeHistoryResponse | null>(null);
  const [mergeTarget, setMergeTarget] = useState("");
  const [mergeReason, setMergeReason] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await workerClient.listCompanies();
      setCompanies(result.companies ?? []);
      if (id) {
        setDetail(await workerClient.getCompanyDetail({ company_id: id }));
      } else {
        setDetail(null);
      }
      setHistory(
        await workerClient.getCompanyMergeHistory(id ? { company_id: id } : {}),
      );
      setError(null);
    } catch (exc) {
      setError(message(exc));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const reviewMerge = () => {
    if (!detail || !mergeTarget) return;
    const target = companies.find((company) => company.company_id === mergeTarget);
    setPending({
      kind: "merge",
      mergedId: mergeTarget,
      survivorId: detail.company_id,
      name: target?.name ?? mergeTarget,
    });
  };

  const confirm = async () => {
    if (!pending) return;
    try {
      if (pending.kind === "merge") {
        const result = await workerClient.mergeCompany({
          company_id: pending.mergedId,
          into_id: pending.survivorId,
          reason: mergeReason,
        });
        setNotice(
          result.changed
            ? `Merged ${pending.name} into this company. The decision is in the audit trail below.`
            : `${pending.name} was already merged into this company; nothing changed.`,
        );
      } else {
        const result = await workerClient.unmergeCompany({ company_id: pending.mergedId });
        setNotice(
          result.changed
            ? `Separated ${pending.name} again. Its postings and applications are counted on their own.`
            : `${pending.name} was already separate; nothing changed.`,
        );
      }
      setPending(null);
      setMergeTarget("");
      setMergeReason("");
      await load();
    } catch (exc) {
      setPending(null);
      setError(message(exc));
    }
  };

  const otherCompanies = companies.filter(
    (company) => company.company_id !== detail?.company_id,
  );
  const records = history?.records ?? [];
  const currentlyMerged = history?.currently_merged ?? {};

  return (
    <div className="space-y-6">
      <PageHeader
        title="Companies"
        description="What has been observed for each company: postings, reposts and your own applications. No company is judged from a small sample."
      />
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          {notice}
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">All companies ({companies.length})</h2>
        {companies.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No companies yet. They are created as postings are discovered.
          </p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-500">
                <th className="py-1">Company</th>
                <th>Postings</th>
                <th>Active</th>
                <th>Your applications</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((company) => (
                <tr
                  key={company.company_id}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                  onClick={() => navigate(`/companies/${company.company_id}`)}
                >
                  <td className="py-2">
                    <Link
                      className="font-medium text-slate-900 underline"
                      to={`/companies/${company.company_id}`}
                    >
                      {company.name}
                    </Link>
                    {company.domain ? (
                      <div className="text-xs text-slate-400">{company.domain}</div>
                    ) : null}
                    {(company.merged_company_count ?? 0) > 0 ? (
                      <div className="text-xs text-slate-400">
                        includes {company.merged_company_count} merged{" "}
                        {(company.merged_company_count ?? 0) === 1 ? "company" : "companies"}
                      </div>
                    ) : null}
                  </td>
                  <td>{company.job_count}</td>
                  <td>{company.active_job_count}</td>
                  <td>{company.application_count}</td>
                  <td className="text-xs text-slate-500">{company.last_seen_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {detail ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">
            {detail.name} · {detail.job_count} postings
          </h2>
          {detail.requested_id ? (
            <p className="mt-1 text-xs text-slate-500">
              You opened {detail.requested_id}, which is merged into {detail.name}. Everything
              below is read under the surviving company.
            </p>
          ) : null}
          <p className="mt-1 text-xs text-slate-500">
            {detail.active_job_count} active · {detail.repost_count} reposts observed · window
            from {detail.window_start ?? "—"}
          </p>

          <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-3">
            <h3 className="text-sm font-semibold text-slate-700">Merge a company into this one</h3>
            <p className="mt-1 text-xs text-slate-500">
              Use this when two names are the same employer. Postings, applications and summaries
              are then read under one company. A merge is a recorded decision and can be undone.
            </p>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <label className="text-xs font-medium text-slate-600">
                Company to merge
                <select
                  className="mt-1 block rounded border border-slate-300 bg-white px-2 py-1 text-sm"
                  value={mergeTarget}
                  onChange={(event) => setMergeTarget(event.target.value)}
                >
                  <option value="">Choose a company…</option>
                  {otherCompanies.map((company) => (
                    <option key={company.company_id} value={company.company_id}>
                      {company.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-medium text-slate-600">
                Reason (optional)
                <input
                  className="mt-1 block rounded border border-slate-300 bg-white px-2 py-1 text-sm"
                  value={mergeReason}
                  onChange={(event) => setMergeReason(event.target.value)}
                  placeholder="Same employer, different name"
                />
              </label>
              <button
                type="button"
                className="rounded bg-slate-900 px-3 py-1 text-sm font-medium text-white disabled:opacity-40"
                disabled={!mergeTarget}
                onClick={reviewMerge}
              >
                Review merge
              </button>
            </div>

            {pending?.kind === "merge" ? (
              <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                <p className="font-medium">
                  Merge {pending.name} into {detail.name}?
                </p>
                <p className="mt-1 text-xs">
                  {pending.name} stops appearing on its own. Its postings and applications are
                  counted under {detail.name}. The decision is kept in the audit trail and can be
                  undone later.
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    className="rounded bg-amber-700 px-3 py-1 text-xs font-medium text-white"
                    onClick={confirm}
                  >
                    Confirm merge
                  </button>
                  <button
                    type="button"
                    className="rounded border border-amber-400 px-3 py-1 text-xs text-amber-900"
                    onClick={() => setPending(null)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <h3 className="mt-4 text-sm font-semibold text-slate-700">Hiring summary</h3>
          {detail.summary ? (
            <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
              {JSON.stringify(detail.summary, null, 2)}
            </pre>
          ) : (
            <p className="mt-1 text-sm text-slate-500">
              No summary yet. Summaries are written once a day when there is enough history.
            </p>
          )}

          <h3 className="mt-4 text-sm font-semibold text-slate-700">Postings</h3>
          <ul className="mt-1 divide-y divide-slate-100 text-sm">
            {(detail.jobs ?? []).map((job) => (
              <li key={job.job_id} className="flex flex-wrap items-center gap-2 py-2">
                <Link className="font-medium text-slate-900 underline" to={`/jobs/${job.job_id}`}>
                  {job.title}
                </Link>
                <span className="text-xs text-slate-500">{job.posting_status}</span>
                <RiskBadge
                  riskClass={job.risk_class}
                  confidence={job.risk_confidence}
                  label={job.risk_label}
                />
                {job.reason ? <span className="text-xs text-slate-400">{job.reason}</span> : null}
              </li>
            ))}
          </ul>

          <h3 className="mt-4 text-sm font-semibold text-slate-700">Your applications</h3>
          {(detail.applications ?? []).length === 0 ? (
            <p className="mt-1 text-sm text-slate-500">You have not tracked an application here.</p>
          ) : (
            <ul className="mt-1 space-y-1 text-sm text-slate-700">
              {(detail.applications ?? []).map((application, index) => (
                <li key={index}>
                  {String(application.status)} · applied{" "}
                  {String(application.applied_at ?? "—")}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Merge audit trail</h2>
        <p className="mt-1 text-xs text-slate-500">
          Every merge and separation is recorded with its reason and time. Records are never
          edited or deleted; a separation is a new decision on top.
        </p>
        {records.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No merge decisions recorded.</p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-500">
                <th className="py-1">When</th>
                <th>Decision</th>
                <th>Company</th>
                <th>Read under</th>
                <th>Reason</th>
                <th>By</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record, index) => {
                const mergedId = String(record.merged_id);
                const survivorId = String(record.survivor_id);
                const active = String(record.action) === "merge";
                const mergedName =
                  companies.find((company) => company.company_id === mergedId)?.name ?? mergedId;
                const survivorName =
                  companies.find((company) => company.company_id === survivorId)?.name ??
                  survivorId;
                return (
                  <tr key={index} className="border-t border-slate-100">
                    <td className="py-2 text-xs text-slate-500">{String(record.created_at)}</td>
                    <td>{String(record.action)}</td>
                    <td>
                      <Link className="underline" to={`/companies/${mergedId}`}>
                        {mergedName}
                      </Link>
                    </td>
                    <td>{survivorName}</td>
                    <td className="text-xs text-slate-500">{String(record.reason ?? "—")}</td>
                    <td className="text-xs text-slate-500">{String(record.created_by ?? "—")}</td>
                    <td>
                      {active && currentlyMerged[mergedId] === survivorId ? (
                        pending?.kind === "unmerge" && pending.mergedId === mergedId ? (
                          <span className="space-x-1 text-xs">
                            <span>Separate?</span>
                            <button
                              type="button"
                              className="rounded bg-amber-700 px-2 py-0.5 text-white"
                              onClick={confirm}
                            >
                              Confirm
                            </button>
                            <button
                              type="button"
                              className="rounded border border-amber-400 px-2 py-0.5"
                              onClick={() => setPending(null)}
                            >
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="text-xs underline"
                            onClick={() =>
                              setPending({
                                kind: "unmerge",
                                mergedId,
                                survivorId,
                                name: mergedName,
                              })
                            }
                          >
                            Undo merge
                          </button>
                        )
                      ) : (
                        <span className="text-xs text-slate-400">superseded</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
