import { useCallback, useEffect, useState } from "react";
import { Copy } from "../../copy/copy";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
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

export function SettingsRoute() {
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

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextSettings, nextPlugins, nextSources] = await Promise.all([
        workerClient.getSettings(),
        workerClient.listPlugins(),
        workerClient.listSources(),
      ]);
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
    <div className="space-y-6">
      <PageHeader title={Copy.settings.title} description={Copy.settings.description} />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {Copy.settings.loadError} {error}
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.settings.general}</h2>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-slate-500">{Copy.settings.appVersion}</dt>
            <dd className="font-medium text-slate-900">{config.app_version ?? Copy.settings.none}</dd>
          </div>
          <div>
            <dt className="text-slate-500">
              {Copy.settings.protocolVersion}
              <InfoTip label={Copy.settings.protocolVersion}>
                Version of the message format used between the app and the local worker.
              </InfoTip>
            </dt>
            <dd className="font-medium text-slate-900">
              {config.protocol_version ?? Copy.settings.none}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">
              {Copy.settings.settingsFormatVersion}
              <InfoTip label={Copy.settings.settingsFormatVersion}>
                {Copy.tooltips.settingsSchema}
              </InfoTip>
            </dt>
            <dd className="font-medium text-slate-900">
              {settings?.schema_version ?? Copy.settings.none}
            </dd>
          </div>
        </dl>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.settings.data}</h2>
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
        </dl>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label={Copy.settings.featureFlags}
        >
          {Copy.settings.featureFlags}
          <InfoTip label={Copy.settings.featureFlags}>{Copy.tooltips.featureFlags}</InfoTip>
        </h2>
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

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">
          Autofill answers
          <InfoTip label="Autofill answers">
            Only these values are typed into application forms. Demographic, legal and
            sensitive fields are never filled, whatever is stored here.
          </InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Stored locally. Leave a field empty to skip it; nothing is inferred from your
          resume or from the posting. For a yes/no or multiple-choice question, store the
          option text ("Yes") — the matching option is picked, and nothing is picked when
          no option matches.
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
          <button
            type="button"
            disabled={busy}
            onClick={() => void saveAnswers()}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            Save answers
          </button>
          <span className="text-xs text-slate-500">{storedAnswers} stored</span>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">
          Answers to open questions
          <InfoTip label="Answers to open questions">
            Open questions are never generated. An answer saved here is typed only into a
            field whose question matches it, and never into a demographic, legal or
            sensitive field.
          </InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Questions like &quot;Why do you want to work here?&quot; stay blank unless you
          save an answer here. The question must match the form&apos;s own wording; case,
          spacing and punctuation are ignored, and a saved answer cannot unlock a blocked
          question.
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
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setSnippetRows((current) =>
                    current.filter((_, position) => position !== index),
                  )
                }
                className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              setSnippetRows((current) => [...current, { question: "", answer: "" }])
            }
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Add a question
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void saveSnippets()}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            Save open-question answers
          </button>
          <span className="text-xs text-slate-500">{storedSnippets} saved</span>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Sources">
          Sources
          <InfoTip label="Sources">{Copy.tooltips.sources}</InfoTip>
        </h2>
        {sources.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No sources configured. Add one on the Discover screen.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {sources.map((source) => (
              <li key={source.source_id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="truncate font-mono text-xs text-slate-700">{source.url}</span>
                <div className="flex shrink-0 items-center gap-2">
                  <label
                    className="flex items-center gap-1 text-xs text-slate-500"
                    title={Copy.tooltips.crawlInterval}
                  >
                    crawl every
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
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void saveInterval(
                        source,
                        intervals[source.source_id] ?? intervalMinutes(source.interval_s),
                      )
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Save interval
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void toggleSource(source)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {source.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void deleteSource(source.source_id)}
                    className="rounded border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    Delete source
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label={Copy.settings.plugins}>
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
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void toggle(plugin)}
                        className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        {plugin.enabled ? Copy.settings.disable : Copy.settings.enable}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
