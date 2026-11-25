import type { WorkflowState } from "../features/workflow/nextStep";
import { nextStep } from "../features/workflow/nextStep";
import { NextStepBar } from "./NextStepBar";

/** A bar whose action is derived from the shared workflow state. */
export function WorkflowNextStepBar({ state }: { state: WorkflowState }) {
  const step = nextStep(state);

  if (step.stage === "review") {
    return (
      <NextStepBar
        text={
          state.matchCount === 0
            ? "No matches yet. Next: collect postings from a source."
            : `${state.newSince || state.matchCount} ${
                state.newSince === 1 ? "new match" : "matches to review"
              }. Next: triage the queue.`
        }
        cta={state.matchCount === 0 ? "Crawl a source" : step.cta}
        to={state.matchCount === 0 ? step.to : "/discover?new=1"}
      />
    );
  }

  if (step.stage === "apply") {
    return (
      <NextStepBar
        text={`${state.readyToApply} ready to apply · ${state.savedCount} on the shortlist.`}
        cta={step.cta}
        to={step.to}
      />
    );
  }

  if (step.stage === "track") {
    return (
      <NextStepBar
        text={
          state.followUps > 0
            ? `${state.followUps} waiting 14+ days · Next: check for a response.`
            : `${state.appliedCount} tracked · Next: record outcomes and check for new matches.`
        }
        cta={step.cta}
        to={step.to}
      />
    );
  }

  return <NextStepBar text={`${step.title}. ${step.reason}`} cta={step.cta} to={step.to} />;
}
