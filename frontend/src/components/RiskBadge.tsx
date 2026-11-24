const CLASS_STYLES: Record<string, string> = {
  likely_active: "bg-emerald-100 text-emerald-800 border-emerald-200",
  likely_stale: "bg-amber-100 text-amber-800 border-amber-200",
  likely_inactive: "bg-rose-100 text-rose-800 border-rose-200",
  needs_verification: "bg-sky-100 text-sky-800 border-sky-200",
  unclassified: "bg-slate-100 text-slate-700 border-slate-200",
};

const CLASS_LABELS: Record<string, string> = {
  likely_active: "Recent activity",
  likely_stale: "May be stale",
  likely_inactive: "May be inactive",
  needs_verification: "Needs verification",
  unclassified: "Not enough evidence",
};

export interface RiskBadgeProps {
  riskClass?: string | null;
  confidence?: number | null;
  label?: string | null;
}

/**
 * A posting-risk chip that opens its own explanation. Risk and confidence are
 * shown separately, and the badge is never a bare percentage: it names the
 * class, then the confidence in the evidence. Missing evidence renders nothing
 * rather than a negative signal.
 */
export function RiskBadge({ riskClass, confidence, label }: RiskBadgeProps) {
  if (!riskClass) return null;
  const className = CLASS_LABELS[riskClass] ?? riskClass.replaceAll("_", " ");
  const conf = typeof confidence === "number" ? confidence : null;
  const confidenceText = conf === null ? "confidence unknown" : `confidence ${conf.toFixed(2)}`;
  return (
    <details className="group relative inline-block shrink-0">
      <summary
        className={`inline-flex cursor-pointer list-none items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
          CLASS_STYLES[riskClass] ?? CLASS_STYLES.unclassified
        }`}
        aria-label={`${label ?? className}, ${confidenceText}. Open the explanation.`}
        data-risk-class={riskClass}
      >
        <span>{className}</span>
        <span className="font-normal opacity-80">{confidenceText}</span>
      </summary>
      <div className="mt-1 w-64 rounded border border-slate-200 bg-white p-2 text-xs text-slate-600 shadow-sm">
        <p className="font-medium text-slate-800">{label ?? className}</p>
        <p className="mt-1">
          Evidence {confidenceText}. Risk and confidence are separate: a high risk with low
          confidence is weak evidence, and a missing signal never raises risk.
        </p>
        <p className="mt-1">
          This is a posting-quality signal, not a prediction about you.
        </p>
      </div>
    </details>
  );
}
