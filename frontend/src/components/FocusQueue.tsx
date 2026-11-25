import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "./Button";
import { CompanyAvatar } from "./CompanyAvatar";
import { RiskBadge } from "./RiskBadge";
import { ScoreMeter } from "./ScoreMeter";
import { isHttpUrl, openExternalUrl } from "../services/openExternal";
import { workerClient } from "../services/workerClient";

export interface FocusJob {
  job_id: string;
  title: string;
  company_name?: string;
  location_text?: string;
  workplace_type?: string;
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string | null;
  fit_score?: number | null;
  risk_class?: string | null;
  risk_confidence?: number | null;
  risk_label?: string | null;
  rank_reasons?: string[];
  canonical_url?: string | null;
  saved?: boolean;
  first_seen_at?: string | null;
}

function salaryLabel(job: FocusJob): string | null {
  const min = typeof job.salary_min === "number" ? job.salary_min : null;
  const max = typeof job.salary_max === "number" ? job.salary_max : null;
  if (min === null && max === null) return null;
  const currency = job.salary_currency ? `${job.salary_currency} ` : "";
  const format = (value: number) => `${currency}${value.toLocaleString()}`;
  if (min !== null && max !== null) {
    return min === max ? format(min) : `${format(min)} – ${format(max)}`;
  }
  return min !== null ? `from ${format(min)}` : `up to ${format(max as number)}`;
}

function ageLabel(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const days = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 86_400_000));
  return days === 0 ? "new today" : `${days} days old`;
}

/**
 * A session queue over the loaded matches. It never claims a job was
 * "reviewed" unless the user saved or dismissed it.
 */
export function FocusQueue({
  jobs,
  total,
  busy,
  onClose,
  onSave,
  onDismiss,
  onLoadMore,
}: {
  jobs: FocusJob[];
  total: number;
  busy: boolean;
  onClose: () => void;
  onSave: (job: FocusJob) => Promise<boolean>;
  onDismiss: (job: FocusJob) => Promise<boolean>;
  onLoadMore?: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [savedCount, setSavedCount] = useState(0);
  const [dismissedCount, setDismissedCount] = useState(0);
  const [description, setDescription] = useState<string | null>(null);
  const [loadingDescription, setLoadingDescription] = useState(false);
  const current = jobs[index] ?? null;

  useEffect(() => {
    if (!current) {
      setDescription(null);
      return;
    }
    let cancelled = false;
    setLoadingDescription(true);
    setDescription(null);
    void workerClient
      .getJobDetail({ job_id: current.job_id })
      .then((detail) => {
        if (cancelled) return;
        const text = (detail.job as { description_text?: unknown })?.description_text;
        setDescription(typeof text === "string" ? text : null);
      })
      .catch(() => {
        if (!cancelled) setDescription(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingDescription(false);
      });
    return () => {
      cancelled = true;
    };
  }, [current]);

  const advance = async (action?: () => Promise<boolean>) => {
    if (busy) return;
    if (action) {
      const ok = await action();
      if (!ok) return;
    }
    setIndex((value) => value + 1);
  };

  const save = () =>
    current
      ? advance(async () => {
          const ok = await onSave(current);
          if (ok) setSavedCount((value) => value + 1);
          return ok;
        })
      : undefined;

  const dismiss = () =>
    current
      ? advance(async () => {
          const ok = await onDismiss(current);
          if (ok) setDismissedCount((value) => value + 1);
          return ok;
        })
      : undefined;

  const open = () => {
    if (current && isHttpUrl(current.canonical_url)) {
      void openExternalUrl(current.canonical_url);
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "escape") onClose();
      if (key === "s") void save();
      if (key === "d") void dismiss();
      if (key === "o") open();
      if (key === "j" || event.key === "ArrowRight") setIndex((value) => value + 1);
      if (key === "k" || event.key === "ArrowLeft") setIndex((value) => Math.max(0, value - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!current) {
    return (
      <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-8 text-center shadow-card">
        <p className="text-lg font-bold text-emerald-900">Focus queue cleared</p>
        <p className="mt-1 text-sm text-emerald-800">
          {savedCount} saved · {dismissedCount} dismissed in this session.
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Link to="/saved?view=ready" className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white">
            Open apply queue
          </Link>
          <Link to="/discover?new=1" className="rounded-md border border-line bg-white px-3 py-1.5 text-sm font-medium text-slate-700">
            Review new matches
          </Link>
          {total > jobs.length && onLoadMore ? (
            <Button variant="secondary" onClick={onLoadMore}>
              Load more matches
            </Button>
          ) : null}
          <Button variant="quiet" onClick={onClose}>
            Back to list
          </Button>
        </div>
      </section>
    );
  }

  const salary = salaryLabel(current);
  const progress = jobs.length === 0 ? 0 : (index / jobs.length) * 100;

  return (
    <section className="rounded-xl border border-line bg-white p-5 shadow-card" aria-label="Focus review">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-brand-700">Focus review</p>
          <p className="tabular text-xs text-slate-500">
            {index + 1} of {jobs.length} loaded · {total} total · {savedCount} saved ·{" "}
            {dismissedCount} dismissed
          </p>
        </div>
        <Button variant="quiet" onClick={onClose}>
          Close (Esc)
        </Button>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${progress}%` }} />
      </div>

      <div className="mt-5 flex items-start gap-3">
        <CompanyAvatar name={current.company_name} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-bold tracking-tight text-slate-900">{current.title}</h2>
          <p className="mt-0.5 text-sm text-slate-600">
            {[current.company_name, current.location_text, current.workplace_type]
              .filter((value) => value && value !== "unknown")
              .join(" · ")}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            {salary ? <span>{salary}</span> : null}
            {current.first_seen_at ? <span>{ageLabel(current.first_seen_at)}</span> : null}
            <ScoreMeter value={current.fit_score} />
            <RiskBadge
              riskClass={current.risk_class}
              confidence={current.risk_confidence}
              label={current.risk_label}
              compact
            />
          </div>
        </div>
      </div>

      <p className="mt-4 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
        {loadingDescription
          ? "Loading the posting…"
          : description
            ? description.slice(0, 1400)
            : "No description stored. Open the original to read it."}
      </p>

      {current.rank_reasons?.length ? (
        <ul className="mt-3 space-y-1 text-xs text-slate-500">
          {current.rank_reasons.slice(0, 2).map((reason, position) => (
            <li key={position}>· {reason}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={busy} onClick={() => void save()}>
          {current.saved ? "Saved ✓ Next" : "Save & next"} <span className="ml-1 opacity-70">s</span>
        </Button>
        <Button variant="danger" disabled={busy} onClick={() => void dismiss()}>
          Dismiss <span className="ml-1 opacity-70">d</span>
        </Button>
        <Button
          variant="secondary"
          disabled={!isHttpUrl(current.canonical_url)}
          onClick={open}
        >
          Open original <span className="ml-1 opacity-70">o</span>
        </Button>
        <Button variant="secondary" onClick={() => setIndex((value) => value + 1)}>
          Skip <span className="ml-1 opacity-70">→</span>
        </Button>
        <Link to={`/jobs/${current.job_id}`} className="px-2 py-1 text-sm font-medium text-brand-700 underline">
          Full details
        </Link>
      </div>
      <p className="mt-3 text-[11px] text-slate-400">
        Keyboard: s save · d dismiss · o open · j/→ next · k/← back. Nothing is submitted.
      </p>
    </section>
  );
}
