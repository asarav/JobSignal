import { useCallback, useEffect, useState } from "react";
import { COUNTS_CHANGED_EVENT } from "../../hooks/useSetupStatus";
import { workerClient } from "../../services/workerClient";
import { useLiveRefresh } from "../../hooks/useLiveRefresh";
import { lastVisitCutoff, readLastVisit } from "./lastVisit";
import type { WorkflowState } from "./nextStep";

export interface WorkflowSnapshot extends WorkflowState {
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

const EMPTY: WorkflowState = {
  hasResume: false,
  hasSources: false,
  hasMatches: false,
  matchCount: 0,
  savedCount: 0,
  appliedCount: 0,
  lastCrawl: null,
  newSince: 0,
  readyToApply: 0,
  followUps: 0,
  appliedLast7Days: 0,
  responsesLast7Days: 0,
};

/**
 * Derive the workflow state from the same worker methods the screens use.
 * Counts are whole-corpus totals, never a page slice or a hardcoded stage.
 */
export function useWorkflowState(): WorkflowSnapshot {
  const [state, setState] = useState<WorkflowState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [ranked, saved, applications, resume, sources, runs] = await Promise.all([
          workerClient.rankedJobs({
            limit: 1,
            ensure_fit: true,
            seen_since: lastVisitCutoff(Date.now(), readLastVisit()),
          }),
          workerClient.listSavedJobs({ view: "ready", limit: 1 }),
          workerClient.listApplications({ limit: 1 }),
          workerClient.listResumeVersions(),
          workerClient.listSources(),
          workerClient.listCrawlRuns({ limit: 1 }),
        ]);
        if (cancelled) return;
        const lastRun = runs.runs[0] ?? null;
        setState({
          hasResume: (resume.versions ?? []).length > 0,
          hasSources: sources.sources.length > 0,
          hasMatches: (ranked.total ?? ranked.jobs?.length ?? 0) > 0,
          matchCount: ranked.total ?? ranked.jobs?.length ?? 0,
          savedCount: saved.total ?? saved.jobs.length,
          appliedCount: applications.summary?.applied ?? applications.total ?? 0,
          lastCrawl: lastRun?.finished_at ?? lastRun?.started_at ?? null,
          newSince: ranked.new_since ?? 0,
          readyToApply: saved.total ?? saved.jobs.length,
          followUps: applications.summary?.follow_up ?? 0,
          appliedLast7Days: applications.summary?.applied_last_7_days ?? 0,
          responsesLast7Days: applications.summary?.responded_last_7_days ?? 0,
        });
        setError(null);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const refresh = useCallback(() => {
    setRevision((value) => value + 1);
    // Navigation badges share these counts; keep them in step without another
    // full-list fetch on the screen that changed the data.
    window.dispatchEvent(new Event(COUNTS_CHANGED_EVENT));
  }, []);

  useLiveRefresh(refresh);

  useEffect(() => {
    // A resume import or a profile save changes this state without navigating.
    const onChanged = () => setRevision((value) => value + 1);
    window.addEventListener(COUNTS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(COUNTS_CHANGED_EVENT, onChanged);
  }, []);

  return { ...state, loading, error, refresh };
}
