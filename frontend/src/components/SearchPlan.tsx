import { Link } from "react-router-dom";
import { WORKFLOW_STAGES, nextStep, stageStatus, type WorkflowState } from "../features/workflow/nextStep";
import { buttonClass } from "./Button";

const STATUS_LABEL = {
  done: "done",
  current: "current step",
  upcoming: "not started",
} as const;

/** The six-stage search loop, derived from real data. No wizard, no modal. */
export function SearchPlan({
  state,
  compact = false,
}: {
  state: WorkflowState;
  compact?: boolean;
}) {
  const step = nextStep(state);

  if (compact) {
    return (
      <section
        aria-label="Your search plan"
        className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-white px-4 py-3 shadow-card"
      >
        <ol className="flex flex-wrap items-center gap-2" aria-label="Workflow stages">
          {WORKFLOW_STAGES.map((stage, index) => {
            const status = stageStatus(state, stage.id, step.stage);
            return (
              <li key={stage.id} className="flex items-center gap-2">
                <span
                  data-status={status}
                  className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
                    status === "current"
                      ? "bg-brand-600 text-white"
                      : status === "done"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-400"
                  }`}
                >
                  {status === "done" ? "✓" : index + 1}
                </span>
                <span
                  className={`text-xs ${
                    status === "current" ? "font-semibold text-brand-700" : "text-slate-500"
                  }`}
                >
                  {stage.label}
                </span>
                {index < WORKFLOW_STAGES.length - 1 ? (
                  <span className="text-slate-300" aria-hidden="true">
                    ·
                  </span>
                ) : null}
              </li>
            );
          })}
        </ol>
        <Link to={step.to} className={buttonClass("primary")}>
          {step.cta}
        </Link>
      </section>
    );
  }

  return (
    <section
      aria-label="Your search plan"
      className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900">Your search plan</h2>
          <p className="mt-1 text-sm text-slate-600">
            <span className="font-medium text-slate-900">{step.title}.</span> {step.reason}
          </p>
        </div>
        <Link to={step.to} className={buttonClass("primary")}>
          {step.cta}
        </Link>
      </div>

      <ol className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {WORKFLOW_STAGES.map((stage, index) => {
          const status = stageStatus(state, stage.id, step.stage);
          return (
            <li
              key={stage.id}
              data-status={status}
              className={`rounded-md border px-3 py-2 ${
                status === "current"
                  ? "border-brand-600 bg-brand-600 text-white"
                  : status === "done"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                    : "border-slate-200 bg-slate-50 text-slate-500"
              }`}
            >
              <span className="block text-xs opacity-80">
                {status === "done" ? "✓" : `${index + 1}.`} {STATUS_LABEL[status]}
              </span>
              <span className="text-sm font-medium">{stage.label}</span>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-xs text-slate-500">
        Steps complete from what is already here. New postings send the loop back to Review.
      </p>
    </section>
  );
}
