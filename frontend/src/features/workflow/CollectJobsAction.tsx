import { useCallback, useEffect, useState } from "react";
import { Button } from "../../components/Button";
import { workerClient } from "../../services/workerClient";

function message(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

/**
 * The Collect step as an action, not a link: add a source and crawl it, or
 * crawl an existing source when one is already configured.
 */
export function CollectJobsAction({
  onComplete,
}: {
  onComplete?: () => void;
}) {
  const [sources, setSources] = useState<Array<{ source_id: string; url: string }> | null>(
    null,
  );
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSources = useCallback(async () => {
    try {
      const result = await workerClient.listSources();
      setSources(result.sources.map((source) => ({ source_id: source.source_id, url: source.url })));
    } catch (exc) {
      setSources([]);
      setError(message(exc));
    }
  }, []);

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  const crawlExisting = async (sourceId: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.runDiscovery({ source_id: sourceId });
      setNote("Crawl finished. New matches appear here as they are stored.");
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
    try {
      const added = await workerClient.addSource({ url: url.trim(), interval_s: 3600 });
      setUrl("");
      await workerClient.runDiscovery({ source_id: added.source_id });
      setNote("Source added and crawled. New matches appear here as they are stored.");
      await loadSources();
      onComplete?.();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  if (sources === null) {
    return <span className="text-xs text-slate-500">Checking sources…</span>;
  }

  return (
    <div className="w-full max-w-xl">
      {sources.length > 0 ? (
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => void crawlExisting(sources[0].source_id)}
        >
          {busy ? "Crawling…" : `Crawl ${sources[0].url}`}
        </Button>
      ) : (
        <div className="flex flex-wrap justify-center gap-2">
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-label="Career page or job board URL"
            placeholder="Paste a careers page URL"
            className="min-w-[14rem] flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
          <Button
            variant="primary"
            disabled={busy || !url.trim()}
            onClick={() => void addAndCrawl()}
          >
            {busy ? "Adding…" : "Add source and crawl"}
          </Button>
        </div>
      )}
      {note ? <p className="mt-2 text-xs text-emerald-700">{note}</p> : null}
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}
