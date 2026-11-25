function band(value: number): "strong" | "fair" | "low" {
  if (value >= 0.75) return "strong";
  if (value >= 0.5) return "fair";
  return "low";
}

const BAND_STYLES = {
  strong: { bar: "bg-emerald-500", text: "text-emerald-700" },
  fair: { bar: "bg-amber-500", text: "text-amber-700" },
  low: { bar: "bg-rose-400", text: "text-rose-700" },
} as const;

/**
 * One fit reading, not a wall of scores. The numeric value stays visible and
 * the colour is only a band, never a prediction.
 */
export function ScoreMeter({
  value,
  label = "Fit",
  showValue = true,
}: {
  value?: number | null;
  label?: string;
  showValue?: boolean;
}) {
  if (typeof value !== "number") {
    return <span className="text-xs text-slate-400">{label} not scored</span>;
  }
  const style = BAND_STYLES[band(value)];
  const segments = Math.max(1, Math.min(5, Math.round(value * 5)));
  return (
    <span className="inline-flex items-center gap-2" title={`${label} ${Math.round(value * 100)}%`}>
      <span className="flex gap-0.5" aria-hidden="true">
        {Array.from({ length: 5 }, (_, index) => (
          <span
            key={index}
            className={`h-1.5 w-3 rounded-full ${index < segments ? style.bar : "bg-slate-200"}`}
          />
        ))}
      </span>
      {showValue ? (
        <span className={`tabular text-xs font-semibold ${style.text}`}>
          {Math.round(value * 100)}
        </span>
      ) : null}
    </span>
  );
}
