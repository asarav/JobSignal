import { useState } from "react";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { BrowserAutofillResponse } from "../../types/generated";

interface PlanItem {
  field_id?: string;
  label?: string;
  value?: string;
  action?: string;
  reason?: string;
}

const ACTION_STYLES: Record<string, string> = {
  fill: "bg-emerald-50 text-emerald-800 border-emerald-200",
  skip: "bg-slate-50 text-slate-700 border-slate-200",
  block: "bg-amber-50 text-amber-900 border-amber-200",
};

const ACTION_LABELS: Record<string, string> = {
  fill: "Will fill",
  skip: "Left blank",
  block: "Never filled",
};

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

/**
 * Review panel for autofill (spec §13.4). It shows exactly what will be typed
 * before anything is touched, and it has no submit action: the browser stays
 * open and the user submits.
 */
export function AutofillPanel({ jobId }: { jobId: string }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [planId, setPlanId] = useState<string | null>(null);
  const [items, setItems] = useState<PlanItem[]>([]);
  const [step, setStep] = useState<{ index: number; count: number; final: boolean | null } | null>(
    null,
  );
  const [report, setReport] = useState<BrowserAutofillResponse | null>(null);
  const [handedOff, setHandedOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const prepare = async () => {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      // Re-checking reuses the open window and re-inspects the *current* DOM;
      // opening a second browser would leave the first one stranded.
      let nextSession = sessionId;
      if (!nextSession) {
        const session = await workerClient.openJobInBrowser({ job_id: jobId });
        nextSession = session.session_id;
        setSessionId(nextSession);
      }
      const form = await workerClient.inspectApplicationForm({ session_id: nextSession });
      const plan = await workerClient.planAutofill({ session_id: nextSession });
      setPlanId(plan.plan_id);
      setItems((plan.items ?? []) as PlanItem[]);
      setStep({
        index: (form.step_index ?? 0) + 1,
        count: form.step_count ?? 1,
        final: form.is_final_step ?? null,
      });
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const fill = async () => {
    if (!sessionId || !planId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await workerClient.autofillForm({
        session_id: sessionId,
        plan_id: planId,
      });
      setReport(result);
      // Filling re-arms the submission guard on the worker side.
      setHandedOff(false);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const handoff = async () => {
    if (!sessionId) return;
    setBusy(true);
    setError(null);
    try {
      await workerClient.handoffBrowserSession({ session_id: sessionId });
      setHandedOff(true);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await workerClient.closeBrowserSession({ session_id: sessionId });
    } catch (exc) {
      setError(message(exc));
    } finally {
      setSessionId(null);
      setPlanId(null);
      setItems([]);
      setStep(null);
      setReport(null);
      setHandedOff(false);
      setBusy(false);
    }
  };

  const fillable = items.filter((item) => item.action === "fill").length;
  const filled = report?.filled ?? [];
  const blocked = report?.blocked ?? [];
  const skipped = report?.skipped ?? [];
  const errors = report?.errors ?? [];

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">Autofill review</h2>
      <p className="mt-1 text-xs text-slate-500">
        Opens the application page in a browser you control. Nothing is submitted:
        review the values, fill the current step, then submit yourself.
      </p>

      {error ? (
        <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      {step ? (
        <p className="mt-2 text-xs text-slate-500">
          Step {step.index} of {step.count}
          {step.final === true
            ? " · this is the last step; you submit it yourself"
            : step.final === false
              ? " · advance the page yourself, then re-check"
              : " · the step count could not be determined"}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void prepare()}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {sessionId ? "Re-check form" : "Check application form"}
        </button>
        {sessionId && planId ? (
          <button
            type="button"
            disabled={busy || fillable === 0}
            onClick={() => void fill()}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Fill {fillable} field{fillable === 1 ? "" : "s"}
          </button>
        ) : null}
        {sessionId && !handedOff ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void handoff()}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            I&apos;ll submit in the browser
          </button>
        ) : null}
        {sessionId ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void close()}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Close browser
          </button>
        ) : null}
      </div>

      {handedOff ? (
        <p className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          Submission guard is off for this window, so the page can send what you
          click. Filling a field again turns it back on.
        </p>
      ) : null}

      {items.length > 0 ? (
        <ul className="mt-4 divide-y divide-slate-100 text-sm">
          {items.map((item) => (
            <li
              key={item.field_id ?? item.label}
              className="flex flex-wrap items-start gap-2 py-2"
            >
              <span
                className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                  ACTION_STYLES[item.action ?? "skip"] ?? ACTION_STYLES.skip
                }`}
              >
                {ACTION_LABELS[item.action ?? "skip"] ?? item.action}
              </span>
              <span className="font-medium text-slate-900">{item.label}</span>
              {item.value ? (
                <span className="text-slate-600">→ {item.value}</span>
              ) : null}
              <span className="text-xs text-slate-500">{item.reason}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {report ? (
        <div className="mt-4 rounded border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          <p>
            Filled {filled.length} field
            {filled.length === 1 ? "" : "s"}. {blocked.length} left blocked,{" "}
            {skipped.length} skipped.
          </p>
          {errors.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {errors.map((entry) => (
                <li key={entry}>{entry}</li>
              ))}
            </ul>
          ) : null}
          <p className="mt-1 text-xs">
            Nothing was submitted. Review the page in the browser window and submit it
            yourself.
          </p>
        </div>
      ) : null}
    </section>
  );
}
