interface TrendPoint {
  day: string;
  n_unique_postings?: number;
  repost_ratio?: number | null;
  inactive_ratio?: number | null;
  sample_tier?: string;
}

interface CompanyTrendChartProps {
  points: TrendPoint[];
  note?: string;
}

/** A dependency-free daily posting-count chart. */
export function CompanyTrendChart({ points, note }: CompanyTrendChartProps) {
  if (points.length === 0) {
    return (
      <p className="mt-1 text-sm text-slate-500">
        {note ?? "No daily summaries yet."}
      </p>
    );
  }

  const values = points.map((point) => point.n_unique_postings ?? 0);
  const max = Math.max(1, ...values);
  const width = 320;
  const height = 96;
  const gap = 4;
  const barWidth = Math.max(2, (width - gap * (points.length - 1)) / points.length);
  const latest = points[points.length - 1];
  // Never trust a caller to withhold: rates are shown only at the adequate
  // tier, even if the value is present.
  const hasRates =
    latest.sample_tier === "adequate" &&
    (latest.repost_ratio != null || latest.inactive_ratio != null);

  return (
    <div className="mt-2">
      <svg
        role="img"
        aria-label="Postings summarised per day"
        viewBox={`0 0 ${width} ${height}`}
        className="h-28 w-full max-w-md"
      >
        {points.map((point, index) => {
          const value = point.n_unique_postings ?? 0;
          const barHeight = Math.round((value / max) * (height - 16));
          return (
            <rect
              key={point.day}
              x={index * (barWidth + gap)}
              y={height - barHeight}
              width={barWidth}
              height={barHeight}
              rx={2}
              className="fill-slate-500"
            >
              <title>
                {point.day}: {value} postings
              </title>
            </rect>
          );
        })}
      </svg>
      <div className="flex justify-between text-xs text-slate-500">
        <span>{points[0].day}</span>
        <span>{latest.day}</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {hasRates
          ? `Latest rates: ${Math.round((latest.repost_ratio ?? 0) * 100)}% reposted, ${Math.round(
              (latest.inactive_ratio ?? 0) * 100,
            )}% assessed inactive.`
          : "Counts only: this sample is below the adequate tier, so no rate is shown."}
      </p>
      {note ? <p className="mt-1 text-xs text-slate-400">{note}</p> : null}
    </div>
  );
}
