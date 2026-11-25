export const WORKFLOW_STAGES = [
  { id: "profile", label: "Profile" },
  { id: "sources", label: "Sources" },
  { id: "review", label: "Review" },
  { id: "shortlist", label: "Shortlist" },
  { id: "apply", label: "Apply" },
  { id: "track", label: "Track" },
] as const;

export type WorkflowStage = (typeof WORKFLOW_STAGES)[number]["id"];

export interface WorkflowState {
  hasResume: boolean;
  hasSources: boolean;
  hasMatches: boolean;
  matchCount: number;
  savedCount: number;
  appliedCount: number;
  /** ISO timestamp of the most recent crawl, or null before the first run. */
  lastCrawl: string | null;
  newSince: number;
  readyToApply: number;
  followUps: number;
  appliedLast7Days: number;
  responsesLast7Days: number;
}

export interface WorkflowStep {
  stage: WorkflowStage;
  title: string;
  reason: string;
  cta: string;
  to: string;
}

export type StageStatus = "done" | "current" | "upcoming";

/**
 * The one next action in the job-search loop. Pure so Today, empty states and
 * tests agree on the order: profile, sources, review, shortlist, apply, track.
 */
export function nextStep(state: WorkflowState): WorkflowStep {
  if (!state.hasResume) {
    return {
      stage: "profile",
      title: "Add your resume",
      reason: "Fit scores need a resume. Everything stays on this computer.",
      cta: "Import resume",
      to: "/resume",
    };
  }
  if (!state.hasSources) {
    return {
      stage: "sources",
      title: "Add a job source",
      reason: "A source is a company careers page or job board the worker can collect.",
      cta: "Add a source",
      to: "/settings?tab=sources&from=plan",
    };
  }
  if (!state.hasMatches) {
    return {
      stage: "review",
      title: "Collect your first jobs",
      reason: state.lastCrawl
        ? `Your last crawl finished ${state.lastCrawl} without a match. Crawl a source again to collect more.`
        : "Your sources are ready; run a crawl so there is something to review.",
      cta: "Crawl a source",
      to: "/settings?tab=sources&from=plan",
    };
  }
  if (state.readyToApply > 0) {
    return {
      stage: "apply",
      title: `${state.readyToApply} ${state.readyToApply === 1 ? "role is" : "roles are"} ready to apply`,
      reason: "Work through the shortlist, then record each application as you send it.",
      cta: "Open apply queue",
      to: "/saved?view=ready",
    };
  }
  if (state.newSince > 0) {
    return {
      stage: "review",
      title: `Review ${state.newSince} new ${state.newSince === 1 ? "match" : "matches"}`,
      reason: "New postings arrived since your last visit. Triage them before they pile up.",
      cta: "Review new matches",
      to: "/discover?new=1",
    };
  }
  if (state.followUps > 0) {
    return {
      stage: "track",
      title: `${state.followUps} ${state.followUps === 1 ? "application is" : "applications are"} waiting 14+ days`,
      reason: "These roles have been in flight for a while. Check for a response or follow up.",
      cta: "Review waiting",
      to: "/applications?view=follow_up",
    };
  }
  if (state.savedCount === 0) {
    return {
      stage: "review",
      title: `Review ${state.matchCount} ${state.matchCount === 1 ? "match" : "matches"}`,
      reason: "Open the roles that look promising and save the ones worth pursuing.",
      cta: "Review matches",
      to: "/discover",
    };
  }
  if (state.appliedCount === 0) {
    return {
      stage: "apply",
      title: "Prepare your first application",
      reason: `${state.savedCount} saved ${state.savedCount === 1 ? "role is" : "roles are"} ready. Open one, review it, and apply yourself.`,
      cta: "Open saved jobs",
      to: "/saved?view=ready",
    };
  }
  return {
    stage: "track",
    title: "Keep your applications current",
    reason: `${state.appliedCount} tracked ${state.appliedCount === 1 ? "application" : "applications"}. Record outcomes and check for new matches.`,
    cta: "Review applications",
    to: "/applications",
  };
}

export function stageStatus(
  state: WorkflowState,
  stage: WorkflowStage,
  current: WorkflowStage,
): StageStatus {
  const complete: Record<WorkflowStage, boolean> = {
    profile: state.hasResume,
    sources: state.hasSources,
    review: state.hasMatches,
    shortlist: state.savedCount > 0,
    apply: state.appliedCount > 0,
    track: state.appliedCount > 0,
  };
  if (stage === current) return "current";
  return complete[stage] ? "done" : "upcoming";
}
