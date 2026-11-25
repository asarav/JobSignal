const STATUS: Record<string, { label: string; className: string; dot: string }> = {
  interested: { label: "Interested", className: "bg-sky-50 text-sky-700 border-sky-200", dot: "bg-sky-500" },
  saved: { label: "Saved", className: "bg-amber-50 text-amber-800 border-amber-200", dot: "bg-amber-500" },
  applied: { label: "Applied", className: "bg-blue-50 text-blue-700 border-blue-200", dot: "bg-blue-500" },
  acknowledged: { label: "Acknowledged", className: "bg-sky-50 text-sky-700 border-sky-200", dot: "bg-sky-500" },
  recruiter_contacted: { label: "Recruiter contact", className: "bg-violet-50 text-violet-700 border-violet-200", dot: "bg-violet-500" },
  interviewing: { label: "Interviewing", className: "bg-violet-100 text-violet-800 border-violet-200", dot: "bg-violet-600" },
  rejected: { label: "Rejected", className: "bg-slate-100 text-slate-600 border-slate-200", dot: "bg-slate-400" },
  withdrawn: { label: "Withdrawn", className: "bg-slate-100 text-slate-600 border-slate-200", dot: "bg-slate-400" },
  offered: { label: "Offer", className: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
  accepted: { label: "Accepted", className: "bg-emerald-100 text-emerald-800 border-emerald-200", dot: "bg-emerald-600" },
};

export function StatusChip({ status }: { status: string }) {
  const style = STATUS[status] ?? {
    label: status.replaceAll("_", " "),
    className: "bg-slate-100 text-slate-600 border-slate-200",
    dot: "bg-slate-400",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${style.className}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {style.label}
    </span>
  );
}
