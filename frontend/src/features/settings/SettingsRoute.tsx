import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button, buttonClass } from "../../components/Button";
import { Disclosure } from "../../components/Disclosure";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Tabs } from "../../components/Tabs";
import { Copy } from "../../copy/copy";
import { deleteAllData } from "../../services/deleteData";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  PluginsListResponsePluginInfo,
  SettingsGetResponse,
  SourcesListResponseSourceInfo,
} from "../../types/generated";

interface SettingsConfig {
  paths?: { data_dir?: string; db_name?: string };
  features?: Record<string, boolean>;
  autofill?: {
    answers?: Record<string, string>;
    snippets?: Record<string, string>;
  };
  app_version?: string;
  protocol_version?: number;
}

/**
 * The answer book shown here. These are the only values the filler may type;
 * nothing is inferred from a resume or a posting (spec §13.3).
 */
const ANSWER_FIELDS: Array<{ key: string; label: string; placeholder?: string }> = [
  { key: "first_name", label: "First name" },
  { key: "middle_name", label: "Middle name" },
  { key: "last_name", label: "Last name" },
  { key: "full_name", label: "Full name", placeholder: "As it should appear on the form" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "address_line1", label: "Address" },
  { key: "city", label: "City" },
  { key: "region", label: "State / region" },
  { key: "postal_code", label: "Postal code" },
  { key: "country", label: "Country" },
  { key: "location", label: "Current location", placeholder: "City, country" },
  { key: "linkedin_url", label: "LinkedIn URL" },
  { key: "github_url", label: "GitHub URL", placeholder: "https://github.com/you" },
  { key: "website", label: "Website / portfolio" },
  { key: "video_url", label: "Video link", placeholder: "A demo, showreel or video portfolio" },
  { key: "current_employer", label: "Current employer" },
  { key: "current_title", label: "Current title" },
  { key: "years_experience", label: "Years of experience", placeholder: "e.g. 9" },
  { key: "school", label: "School / university" },
  { key: "degree", label: "Degree" },
  { key: "major", label: "Field of study" },
  { key: "gpa", label: "GPA" },
  { key: "resume_file", label: "Résumé path", placeholder: "Absolute path to a PDF or DOCX" },
  { key: "cover_letter_file", label: "Cover letter path", placeholder: "Absolute path to a PDF or DOCX" },
  { key: "work_authorization", label: "Work authorization", placeholder: "Yes / No, or your own words" },
  { key: "visa_sponsorship", label: "Visa sponsorship", placeholder: "Yes / No, or your own words" },
  { key: "notice_period", label: "Notice period", placeholder: "e.g. 4 weeks" },
  { key: "start_date", label: "Earliest start date", placeholder: "e.g. 1 March 2027" },
  { key: "desired_salary", label: "Desired salary", placeholder: "Your own words" },
  { key: "relocation", label: "Willing to relocate", placeholder: "Yes / No, or your own words" },
];

/** Crawl interval in whole minutes, as shown in the input. */
function intervalMinutes(intervalS: number | undefined): string {
  return String(Math.round((intervalS ?? 0) / 60));
}

const PLUGIN_HEALTH_LABELS: Record<string, string> = {
  closed: "Healthy",
  half_open: "Recovering",
  open: "Failing",
};

const TABS = [
  { id: "sources", label: "Sources" },
  { id: "data", label: "Data & backup" },
  { id: "answers", label: "Application answers" },
  { id: "plugins", label: "Plugins & flags" },
];

export function SettingsRoute() {
  const [params, setParams] = useSearchParams();
  const activeTab = TABS.some((tab) => tab.id === params.get("tab"))
    ? (params.get("tab") as string)
    : "sources";
  const fromPlan = params.get("from") === "plan";

  const [settings, setSettings] = useState<SettingsGetResponse | null>(null);
  const [plugins, setPlugins] = useState<PluginsListResponsePluginInfo[]>([]);
  const [sources, setSources] = useState<SourcesListResponseSourceInfo[]>([]);
  const [intervals, setIntervals] = useState<Record<string, string>>({});
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [snippetRows, setSnippetRows] = useState<Array<{ question: string; answer: string }>>(
    [],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [backupRuns, setBackupRuns] = useState<Record<string, unknown>[]>([]);
  const [backupMessage, setBackupMessage] = useState<string | null>(null);
  const [importPath, setImportPath] = useState("");
  const [importConfirmed, setImportConfirmed] = useState(false);
  const [deleteText, setDeleteText] = useState("");
  const [deleteMessage, setDeleteMessage] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [lastAutoRun, setLastAutoRun] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextSettings, nextPlugins, nextSources, nextBackups] = await Promise.all([
        workerClient.getSettings(),
        workerClient.listPlugins(),
        workerClient.listSources(),
        workerClient.getBackupHistory({ limit: 10 }),
      ]);
      setBackupRuns(nextBackups.runs ?? []);
      setSettings(nextSettings);
      setPlugins(nextPlugins.plugins);
      setSources(nextSources.sources);
      setAnswers({
        ...((nextSettings.config as SettingsConfig).autofill?.answers ?? {}),
      });
      setSnippetRows(
        Object.entries((nextSettings.config as SettingsConfig).autofill?.snippets ?? {})
          .filter(([, answer]) => answer.trim() !== "")
          .map(([question, answer]) => ({ question, answer })),
      );
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const setTab = (tab: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", tab);
    setParams(next, { replace: true });
  };

  const exportBackup = useCallback(async () => {
    setBusy(true);
    setBackupMessage(null);
    try {
      const result = await workerClient.exportBackup({});
      setBackupMessage(`Saved ${result.path} (${result.checksum.slice(0, 12)}…)`);
      await load();
    } catch (exc) {
      setBackupMessage(
        exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc),
      );
    } finally {
      setBusy(false);
    }
  }, [load]);

  const importBackup = useCallback(async () => {
    setBusy(true);
    setBackupMessage(null);
    try {
      const result = await workerClient.importBackup({
        path: importPath.trim(),
        confirm: true,
      });
      const counts = result.counts ?? {};
      const tables = Object.keys(counts).length;
      const rows = Object.values(counts).reduce((total, value) => total + value, 0);
      setBackupMessage(
        result.verified
          ? `Restored ${tables} tables (${rows} rows) and checked every row count.`
          : "The restore did not verify; nothing was kept.",
      );
      await load();
    } catch (exc) {
      setBackupMessage(
        exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc),
      );
    } finally {
      setBusy(false);
    }
  }, [importPath, load]);

  const removeAllData = useCallback(async () => {
    setDeleting(true);
    setDeleteMessage(null);
    try {
      await deleteAllData(deleteText);
      setDeleteText("");
      setDeleteMessage("Local data deleted. The worker is restarting with an empty database…");
      window.setTimeout(() => window.location.reload(), 2500);
    } catch (exc) {
      setDeleteMessage(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDeleting(false);
    }
  }, [deleteText]);

  const toggle = async (plugin: PluginsListResponsePluginInfo) => {
    setBusy(true);
    try {
      if (plugin.enabled) {
        await workerClient.disablePlugin({ plugin_id: plugin.plugin_id });
      } else {
        await workerClient.enablePlugin({ plugin_id: plugin.plugin_id });
      }
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const toggleSource = async (source: SourcesListResponseSourceInfo) => {
    setBusy(true);
    try {
      await workerClient.setSourceEnabled({ source_id: source.source_id, enabled: !source.enabled });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const deleteSource = async (sourceId: string) => {
    setBusy(true);
    try {
      await workerClient.deleteSource({ source_id: sourceId });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveInterval = async (source: SourcesListResponseSourceInfo, minutes: string) => {
    const value = Number(minutes);
    if (!Number.isFinite(value) || value < 0) return;
    setBusy(true);
    try {
      await workerClient.setSourceSchedule({
        source_id: source.source_id,
        interval_s: value * 60,
      });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const crawlSource = async (sourceId: string) => {
    setBusy(true);
    setLastRun(null);
    try {
      const result = await workerClient.runDiscovery({ source_id: sourceId });
      setLastRun(result as unknown as Record<string, unknown>);
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  /** The plan's "add a source and crawl it" step, in one action. */
  const addAndCrawl = async () => {
    if (!sourceUrl.trim()) return;
    setBusy(true);
    setError(null);
    setLastRun(null);
    try {
      const added = await workerClient.addSource({
        url: sourceUrl.trim(),
        interval_s: 3600,
      });
      setSourceUrl("");
      const result = await workerClient.runDiscovery({ source_id: added.source_id });
      setLastRun(result as unknown as Record<string, unknown>);
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const autoDiscover = async () => {
    setBusy(true);
    setLastAutoRun(null);
    try {
      setLastAutoRun((await workerClient.autoDiscover()) as unknown as Record<string, unknown>);
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveAnswers = async () => {
    setBusy(true);
    setError(null);
    try {
      // Keep empty strings rather than dropping keys: settings patches are
      // deep-merged, so a dropped key would silently keep its old value.
      const patch: Record<string, string> = {};
      for (const field of ANSWER_FIELDS) {
        patch[field.key] = answers[field.key] ?? "";
      }
      await workerClient.updateSettings({ patch: { autofill: { answers: patch } } });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveSnippets = async () => {
    setBusy(true);
    setError(null);
    try {
      const patch: Record<string, string> = {};
      for (const row of snippetRows) {
        const question = row.question.trim();
        if (!question) continue;
        patch[question] = row.answer;
      }
      // Settings patches deep-merge, so a question removed here has to be
      // cleared explicitly or its old answer would keep being typed.
      const stored = (settings?.config as SettingsConfig | undefined)?.autofill?.snippets ?? {};
      for (const question of Object.keys(stored)) {
        if (!(question in patch)) patch[question] = "";
      }
      await workerClient.updateSettings({ patch: { autofill: { snippets: patch } } });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const config = (settings?.config ?? {}) as SettingsConfig;
  const featureFlags = config.features ?? {};
  const overrides = settings?.overrides ?? {};
  const storedAnswers = Object.values(answers).filter((value) => value.trim() !== "").length;
  const storedSnippets = snippetRows.filter(
    (row) => row.question.trim() !== "" && row.answer.trim() !== "",
  ).length;

  return (
    <div className="space-y-5">
      <PageHeader
        title={Copy.settings.title}
        description="Sources, your data, application answers and optional plugins."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {Copy.settings.loadError} {error}
        </div>
      ) : null}

      <Tabs label="Settings sections" items={TABS} active={activeTab} onChange={setTab} />

      {activeTab === "sources" ? (
        <div className="space-y-4">
          {fromPlan ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-300 bg-slate-100 px-3 py-2 text-sm text-slate-700">
              <span>Add a source and crawl it once. New matches appear on Find jobs.</span>
              <Link to="/" className="underline">
                Back to Today
              </Link>
            </div>
          ) : null}

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900" aria-label="Sources">
              Sources
              <InfoTip label="Sources">{Copy.tooltips.sources}</InfoTip>
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Paste a company careers page or job board listing. Adding it crawls it once
              immediately.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="https://boards.greenhouse.io/your-company"
                aria-label="Career page or job board URL"
                value={sourceUrl}
                onChange={(event) => setSourceUrl(event.target.value)}
              />
              <Button
                variant="primary"
                disabled={busy || !sourceUrl.trim()}
                onClick={() => void addAndCrawl()}
              >
                {busy ? "Adding…" : "Add source and crawl"}
              </Button>
            </div>

            {lastRun ? (
              <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
                Crawl finished: {String(lastRun.discovered ?? 0)} postings found,{" "}
                {String(lastRun.new ?? 0)} new, {String(lastRun.updated ?? 0)} updated,{" "}
                {String(lastRun.errors ?? 0)} errors.
              </div>
            ) : null}

            {sources.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">
                No sources configured yet. Add one above to start collecting jobs.
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-slate-100">
                {sources.map((source) => (
                  <li
                    key={source.source_id}
                    className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm"
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700">
                      {source.url}
                      {source.tier ? (
                        <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 font-sans text-xs text-slate-600">
                          {source.tier}
                        </span>
                      ) : null}
                    </span>
                    <div className="flex flex-wrap items-center gap-2">
                      <label
                        className="flex items-center gap-1 text-xs text-slate-500"
                        title={Copy.tooltips.crawlInterval}
                      >
                        every
                        <input
                          type="number"
                          min={0}
                          step={5}
                          value={intervals[source.source_id] ?? intervalMinutes(source.interval_s)}
                          onChange={(event) =>
                            setIntervals((current) => ({
                              ...current,
                              [source.source_id]: event.target.value,
                            }))
                          }
                          className="w-16 rounded border border-slate-300 px-2 py-1 text-xs"
                          aria-label={`Crawl interval in minutes for ${source.url}`}
                        />
                        min
                      </label>
                      <Button
                        disabled={busy}
                        onClick={() =>
                          void saveInterval(
                            source,
                            intervals[source.source_id] ?? intervalMinutes(source.interval_s),
                          )
                        }
                      >
                        Save interval
                      </Button>
                      <Button disabled={busy} onClick={() => void crawlSource(source.source_id)}>
                        Crawl now
                      </Button>
                      <Button disabled={busy} onClick={() => void toggleSource(source)}>
                        {source.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        className="border-red-300 text-red-700 hover:bg-red-50"
                        disabled={busy}
                        onClick={() => void deleteSource(source.source_id)}
                      >
                        Delete
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Disclosure
            summary="Auto-discovery"
            hint="Let the worker find new career sites and re-crawl sources that are due."
          >
            <p className="text-sm text-slate-600">
              A pass looks for new career sites, registers them, and crawls sources whose
              interval is due. It runs in bounded background passes.
            </p>
            <Button
              variant="primary"
              className="mt-3"
              disabled={busy}
              onClick={() => void autoDiscover()}
            >
              {busy ? "Running pass…" : "Run discovery pass"}
            </Button>
            {lastAutoRun ? (
              <p className="mt-3 text-sm text-slate-600">
                {String(lastAutoRun.discovered ?? 0)} sites found,{" "}
                {String(lastAutoRun.registered ?? 0)} new sources,{" "}
                {String(lastAutoRun.probed ?? 0)} probed (
                {String(lastAutoRun.live ?? 0)} live,{" "}
                {String(lastAutoRun.empty ?? 0)} empty,{" "}
                {String(lastAutoRun.dead ?? 0)} dead),{" "}
                {String(lastAutoRun.queued ?? 0)} queued,{" "}
                {String(lastAutoRun.crawled ?? 0)} crawled,{" "}
                {String(lastAutoRun.failed ?? 0)} failed.
              </p>
            ) : null}
          </Disclosure>
        </div>
      ) : null}

      {activeTab === "data" ? (
        <div className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">{Copy.settings.data}</h2>
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-slate-500">{Copy.settings.dataDir}</dt>
                <dd className="break-all font-mono text-xs text-slate-900">
                  {config.paths?.data_dir || Copy.settings.none}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">{Copy.settings.database}</dt>
                <dd className="font-mono text-xs text-slate-900">
                  {config.paths?.db_name ?? Copy.settings.none}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">{Copy.settings.appVersion}</dt>
                <dd>{config.app_version ?? Copy.settings.none}</dd>
              </div>
              <div>
                <dt className="text-slate-500">{Copy.settings.protocolVersion}</dt>
                <dd>{config.protocol_version ?? Copy.settings.none}</dd>
              </div>
              <div>
                <dt className="text-slate-500">{Copy.settings.settingsFormatVersion}</dt>
                <dd>{settings?.schema_version ?? Copy.settings.none}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-slate-500">
              Stored locally. <Link to="/diagnostics" className="underline">Diagnostics</Link>{" "}
              holds worker health and recent tasks.
            </p>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">
              {Copy.settings.backup}
              <InfoTip label={Copy.settings.backup}>{Copy.tooltips.backup}</InfoTip>
            </h2>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={busy} onClick={() => void exportBackup()}>
                {Copy.settings.exportBackup}
              </Button>
              {backupMessage ? (
                <span className="text-xs text-slate-600">{backupMessage}</span>
              ) : null}
            </div>
            <div className="mt-4 flex flex-col gap-2">
              <label className="text-xs text-slate-500" htmlFor="backup-import-path">
                {Copy.settings.importPath}
              </label>
              <input
                id="backup-import-path"
                type="text"
                value={importPath}
                placeholder="backups/jobsignal-backup-20260101T000000Z.json"
                onChange={(event) => setImportPath(event.target.value)}
                className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
              />
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={importConfirmed}
                  onChange={(event) => setImportConfirmed(event.target.checked)}
                />
                {Copy.settings.importConfirm}
              </label>
              <Button
                className="self-start"
                disabled={busy || !importConfirmed || importPath.trim() === ""}
                onClick={() => void importBackup()}
              >
                {Copy.settings.importBackup}
              </Button>
            </div>
            <h3 className="mt-4 text-sm font-semibold text-slate-900">
              {Copy.settings.backupHistory}
            </h3>
            {backupRuns.length === 0 ? (
              <p className="mt-2 text-xs text-slate-500">{Copy.settings.noBackups}</p>
            ) : (
              <ul className="mt-2 space-y-1 text-xs text-slate-600">
                {backupRuns.map((run) => (
                  <li key={String(run.id)}>
                    {String(run.kind)} · {String(run.created_at)} · {String(run.path)}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-lg border border-red-300 bg-red-50 p-4">
            <h2 className="text-base font-semibold text-red-900">Delete all local data</h2>
            <p className="mt-1 text-xs text-red-800">
              Removes every job, source, résumé, backup and log from the local data folder,
              then restarts the worker on an empty database. Export a backup first if you may
              want this data again. This cannot be undone.
            </p>
            <label className="mt-3 block text-xs font-medium text-red-900" htmlFor="delete-all">
              Type DELETE to confirm
            </label>
            <input
              id="delete-all"
              type="text"
              value={deleteText}
              autoComplete="off"
              onChange={(event) => setDeleteText(event.target.value)}
              className="mt-1 rounded border border-red-300 px-2 py-1 font-mono text-sm"
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                className="border-red-600 bg-red-600 text-white"
                disabled={deleting || deleteText.trim() !== "DELETE"}
                onClick={() => void removeAllData()}
              >
                {deleting ? "Deleting…" : "Delete all local data"}
              </Button>
              {deleteMessage ? (
                <span className="text-xs text-red-900">{deleteMessage}</span>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "answers" ? (
        <div className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">
              Autofill answers
              <InfoTip label="Autofill answers">
                Only these values are typed into application forms. Demographic, legal and
                sensitive fields are never filled, whatever is stored here.
              </InfoTip>
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Stored locally. Leave a field empty to skip it; nothing is inferred. For a
              yes/no question, store the option text (&quot;Yes&quot;).
            </p>
            <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {ANSWER_FIELDS.map((field) => (
                <label key={field.key} className="flex flex-col gap-1">
                  <span className="text-xs text-slate-500">{field.label}</span>
                  <input
                    type="text"
                    value={answers[field.key] ?? ""}
                    placeholder={field.placeholder}
                    onChange={(event) =>
                      setAnswers((current) => ({ ...current, [field.key]: event.target.value }))
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-sm"
                  />
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <Button variant="primary" disabled={busy} onClick={() => void saveAnswers()}>
                Save answers
              </Button>
              <span className="text-xs text-slate-500">{storedAnswers} stored</span>
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">
              Answers to open questions
              <InfoTip label="Answers to open questions">
                Open questions are never generated. An answer saved here is typed only into a
                field whose question matches it, and never into a sensitive field.
              </InfoTip>
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Questions like &quot;Why do you want to work here?&quot; stay blank unless you
              save an answer here, and the wording must match the form.
            </p>
            <ul className="mt-3 space-y-2">
              {snippetRows.map((row, index) => (
                <li key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <input
                    type="text"
                    aria-label={`Question ${index + 1}`}
                    value={row.question}
                    placeholder="Question as the form asks it"
                    onChange={(event) =>
                      setSnippetRows((current) =>
                        current.map((entry, position) =>
                          position === index
                            ? { ...entry, question: event.target.value }
                            : entry,
                        ),
                      )
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-sm"
                  />
                  <textarea
                    aria-label={`Answer ${index + 1}`}
                    rows={2}
                    value={row.answer}
                    placeholder="Your answer"
                    onChange={(event) =>
                      setSnippetRows((current) =>
                        current.map((entry, position) =>
                          position === index
                            ? { ...entry, answer: event.target.value }
                            : entry,
                        ),
                      )
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-sm"
                  />
                  <Button
                    disabled={busy}
                    onClick={() =>
                      setSnippetRows((current) =>
                        current.filter((_, position) => position !== index),
                      )
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                disabled={busy}
                onClick={() => setSnippetRows((current) => [...current, { question: "", answer: "" }])}
              >
                Add a question
              </Button>
              <Button variant="primary" disabled={busy} onClick={() => void saveSnippets()}>
                Save open-question answers
              </Button>
              <span className="text-xs text-slate-500">{storedSnippets} saved</span>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "plugins" ? (
        <div className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900" aria-label={Copy.settings.plugins}>
              {Copy.settings.plugins}
              <InfoTip label={Copy.settings.plugins}>{Copy.tooltips.plugins}</InfoTip>
            </h2>
            {plugins.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">{Copy.settings.pluginNone}</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-2 py-2">{Copy.settings.pluginId}</th>
                      <th className="px-2 py-2">{Copy.settings.pluginKind}</th>
                      <th className="px-2 py-2">{Copy.settings.pluginVersion}</th>
                      <th className="px-2 py-2">{Copy.settings.pluginHealth}</th>
                      <th className="px-2 py-2">{Copy.settings.pluginHosts}</th>
                      <th className="px-2 py-2">{Copy.settings.pluginEnabled}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plugins.map((plugin) => (
                      <tr key={plugin.plugin_id} className="border-b border-slate-100">
                        <td className="px-2 py-2 font-mono text-xs">{plugin.plugin_id}</td>
                        <td className="px-2 py-2">{plugin.kind}</td>
                        <td className="px-2 py-2">{plugin.version}</td>
                        <td className="px-2 py-2">
                          {PLUGIN_HEALTH_LABELS[
                            String((plugin.health as { state?: string }).state ?? "")
                          ] ?? "Unknown"}
                        </td>
                        <td className="px-2 py-2 font-mono text-xs">
                          {plugin.network_hosts.length > 0 ? plugin.network_hosts.join(", ") : "-"}
                        </td>
                        <td className="px-2 py-2">
                          <Button disabled={busy} onClick={() => void toggle(plugin)}>
                            {plugin.enabled ? Copy.settings.disable : Copy.settings.enable}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">{Copy.settings.featureFlags}</h2>
            <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(featureFlags).map(([flag, enabled]) => (
                <li
                  key={flag}
                  className="flex items-center justify-between rounded border border-slate-100 bg-slate-50 px-3 py-2"
                >
                  <span className="font-mono text-xs text-slate-700">{flag}</span>
                  <span className={enabled ? "text-emerald-700" : "text-slate-500"}>
                    {enabled ? "enabled" : "disabled"}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              {Copy.settings.overrides}:{" "}
              {Object.keys(overrides).length === 0
                ? Copy.settings.noOverrides
                : Object.keys(overrides).join(", ")}
            </p>
          </section>
        </div>
      ) : null}

      <p className="text-xs text-slate-500">
        <Link to="/diagnostics" className={buttonClass("quiet")}>
          Open Diagnostics
        </Link>
      </p>
    </div>
  );
}
