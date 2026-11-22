import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { JobsGetDetailResponse } from "../../types/generated";

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value ? value : fallback;
}

const REPOST_CLASS_LABELS: Record<string, string> = {
  exact_duplicate: "Exact duplicate",
  reposted_identical: "Repost, same content",
  materially_updated: "Repost, material change",
  similar_distinct: "Similar but separate",
};

const RELATION_LABELS: Record<string, string> = {
  similar: "Similar role",
  duplicate: "Duplicate",
  repost: "Repost",
  user_merged: "Merged by you",
  user_unmerged: "Separated by you",
};

function formatTimestamp(value: unknown): string {
  const raw = text(value, "—");
  const parsed = new Date(raw);
  return raw === "—" || Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString();
}

export function JobDetailRoute() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<JobsGetDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!id) return;
      try {
        const result = await workerClient.getJobDetail({ job_id: id });
        if (!cancelled) {
          setDetail(result);
          setError(null);
        }
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
  }, [id]);

  const job = detail?.job ?? {};
  const reposts = detail?.reposts ?? [];
  const relations = detail?.relations ?? [];
  const observations = detail?.observations ?? [];
  const snapshots = detail?.snapshots ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title={text(job.title, "Job detail")}
        description={text(job.canonical_url, "Job history and duplicate relations.")}
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm text-sm text-slate-700">
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Posting status</dt>
            <dd>{text(job.posting_status, "unknown")}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">First seen</dt>
            <dd>{formatTimestamp(job.first_seen_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Last seen</dt>
            <dd>{formatTimestamp(job.last_seen_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">
              Times observed
              <InfoTip label="Times observed">{Copy.tooltips.observations}</InfoTip>
            </dt>
            <dd>{observations.length}</dd>
          </div>
        </dl>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Repost history">
          Repost history
          <InfoTip label="Repost history">{Copy.tooltips.reposts}</InfoTip>
        </h2>
        {reposts.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No reposts detected. A repost appears when the same posting shows up at a new
            URL or job-board id.
          </p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {reposts.map((event, index) => (
              <li key={index}>
                {REPOST_CLASS_LABELS[text(event.class)] ?? text(event.class, "Repost")} ·
                confidence{" "}
                <span title="How strongly the evidence supports this classification.">
                  {typeof event.confidence === "number" ? event.confidence.toFixed(2) : "—"}
                </span>
                {event.material ? " · material change" : ""}
                {event.posted_date_reset ? " · posted date reset" : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label="Duplicates and relations"
        >
          Duplicates & relations
          <InfoTip label="Duplicates and relations">{Copy.tooltips.relations}</InfoTip>
        </h2>
        {relations.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No related jobs. Relations appear when a posting is linked as a duplicate,
            repost or similar role.
          </p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {relations.map((relation, index) => (
              <li key={index}>
                {RELATION_LABELS[text(relation.relation)] ?? text(relation.relation, "Related")}{" "}
                · job{" "}
                {text(relation.related_job_id, text(relation.job_id, "—"))}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label={`Snapshots (${snapshots.length})`}
        >
          Snapshots ({snapshots.length})
          <InfoTip label="Snapshots">{Copy.tooltips.snapshots}</InfoTip>
        </h2>
        {snapshots.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No content snapshots recorded yet.</p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {snapshots.map((snapshot, index) => (
              <li key={index}>
                {formatTimestamp(snapshot.created_at)} · version{" "}
                {text(snapshot.content_hash, "").slice(0, 12)}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
