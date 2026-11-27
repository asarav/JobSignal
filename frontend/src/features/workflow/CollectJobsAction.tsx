import { useState } from "react";
import { Button } from "../../components/Button";
import { workerClient } from "../../services/workerClient";

function message(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

/**
 * The Collect step: run one discovery pass over every board, or add a single
 * board the user already knows.  A pass is what finds new boards, so it is the
 * primary action instead of re-crawling only the first configured source.
 */
export function CollectJobsAction({ onComplete }: { onComplete?: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runPass = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await workerClient.autoDiscover();
      setNote(
        `Pass finished: ${result.registered ?? 0} new boards, ` +
          `${result.crawled ?? 0} boards crawled, ` +
          `${result.postings_harvested ?? 0} postings found. ` +
          "It keeps running in the background, so more matches appear over time.",
      );
      onComplete?.();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const addAndCrawl = async () => {
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const added = await workerClient.addSource({ url: url.trim(), interval_s: 3600 });
      setUrl("");
      await workerClient.runDiscovery({ source_id: added.source_id });
      setNote("Board added and crawled. It joins the boards every later pass crawls.");
      onComplete?.();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full max-w-xl">
      <Button variant="primary" disabled={busy} onClick={() => void runPass()}>
        {busy ? "Finding jobs…" : "Find new jobs everywhere"}
      </Button>
      <p className="mt-2 text-xs text-slate-500">
        One pass crawls every due board and looks for new ones. It is bounded per pass, so
        run it again or let the background loop continue.
      </p>
      <div className="mt-3 flex flex-wrap justify-center gap-2">
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          aria-label="Career page or job board URL"
          placeholder="Or paste one careers page URL"
          className="min-w-[14rem] flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
        />
        <Button
          variant="secondary"
          disabled={busy || !url.trim()}
          onClick={() => void addAndCrawl()}
        >
          {busy ? "Adding…" : "Add one board and crawl it"}
        </Button>
      </div>
      {note ? <p className="mt-2 text-xs text-emerald-700">{note}</p> : null}
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}
