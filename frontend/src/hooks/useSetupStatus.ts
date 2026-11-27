import { useCallback, useEffect, useState } from "react";
import { workerClient } from "../services/workerClient";

export interface SetupStatus {
  /** False until the worker answered once; distinct from "nothing set up". */
  checked: boolean;
  hasResume: boolean;
  hasSources: boolean;
  /** Short, human-readable names for the setup steps still missing. */
  missing: string[];
  refresh: () => void;
}

const EMPTY: SetupStatus = {
  checked: false,
  hasResume: false,
  hasSources: false,
  missing: [],
  refresh: () => undefined,
};

/** The event dispatched whenever setup or list counts change elsewhere. */
export const COUNTS_CHANGED_EVENT = "jobsignal:counts-changed";

/**
 * What still needs doing before matching can work, with the reason attached.
 * The sidebar used to say only "Setup needed", and its state went stale after
 * an import on the same screen.
 */
export function useSetupStatus(): SetupStatus {
  const [state, setState] = useState<Omit<SetupStatus, "refresh">>({
    checked: EMPTY.checked,
    hasResume: EMPTY.hasResume,
    hasSources: EMPTY.hasSources,
    missing: EMPTY.missing,
  });
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [resume, sources] = await Promise.all([
          workerClient.listResumeVersions(),
          workerClient.listSources(),
        ]);
        if (cancelled) return;
        const hasResume = (resume.versions ?? []).length > 0;
        const hasSources = (sources.sources ?? []).length > 0;
        const missing: string[] = [];
        if (!hasResume) missing.push("import a resume");
        if (!hasSources) missing.push("add a job source");
        setState({ checked: true, hasResume, hasSources, missing });
      } catch {
        if (!cancelled) {
          setState({ checked: false, hasResume: false, hasSources: false, missing: [] });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [revision]);

  useEffect(() => {
    const onChanged = () => setRevision((value) => value + 1);
    window.addEventListener(COUNTS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(COUNTS_CHANGED_EVENT, onChanged);
  }, []);

  const refresh = useCallback(() => {
    setRevision((value) => value + 1);
  }, []);

  return { ...state, refresh };
}

/** Announce a change other screens should pick up without a reload. */
export function announceCountsChanged(): void {
  window.dispatchEvent(new Event(COUNTS_CHANGED_EVENT));
}
