export function Metric({
  label,
  value,
  note,
  tone = "brand",
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "brand" | "success" | "warning" | "danger" | "neutral";
}) {
  const tones = {
    brand: "text-brand-700",
    success: "text-emerald-700",
    warning: "text-amber-700",
    danger: "text-rose-700",
    neutral: "text-slate-700",
  } as const;
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className={`tabular mt-0.5 text-xl font-bold tracking-tight ${tones[tone]}`}>{value}</p>
      {note ? <p className="mt-0.5 truncate text-xs text-slate-500">{note}</p> : null}
    </div>
  );
}
