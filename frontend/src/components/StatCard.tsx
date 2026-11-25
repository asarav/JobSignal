import { Link } from "react-router-dom";

export function StatCard({
  label,
  value,
  hint,
  to,
}: {
  label: string;
  value: string | number;
  hint?: string;
  to?: string;
}) {
  const content = (
    <>
      <span className="block text-xs uppercase tracking-wide text-slate-500">{label}</span>
      <span className="mt-1 block text-2xl font-semibold text-slate-900">{value}</span>
      {hint ? <span className="mt-1 block text-xs text-slate-500">{hint}</span> : null}
    </>
  );
  const className =
    "block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-300";
  return to ? (
    <Link to={to} className={className}>
      {content}
    </Link>
  ) : (
    <div className={className}>{content}</div>
  );
}
