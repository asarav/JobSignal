import { useCallback, useEffect, useState } from "react";
import { Copy } from "../../copy/copy";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  PluginsListResponsePluginInfo,
  SettingsGetResponse,
} from "../../types/generated";

interface SettingsConfig {
  paths?: { data_dir?: string; db_name?: string };
  features?: Record<string, boolean>;
  app_version?: string;
  protocol_version?: number;
}

export function SettingsRoute() {
  const [settings, setSettings] = useState<SettingsGetResponse | null>(null);
  const [plugins, setPlugins] = useState<PluginsListResponsePluginInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextSettings, nextPlugins] = await Promise.all([
        workerClient.getSettings(),
        workerClient.listPlugins(),
      ]);
      setSettings(nextSettings);
      setPlugins(nextPlugins.plugins);
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

  const config = (settings?.config ?? {}) as SettingsConfig;
  const featureFlags = config.features ?? {};
  const overrides = settings?.overrides ?? {};

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
            <dt className="text-slate-500">{Copy.settings.protocolVersion}</dt>
            <dd className="font-medium text-slate-900">
              {config.protocol_version ?? Copy.settings.none}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">{Copy.settings.schemaVersion}</dt>
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
        <h2 className="text-lg font-semibold text-slate-900">{Copy.settings.featureFlags}</h2>
        <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(featureFlags).map(([flag, enabled]) => (
            <li
              key={flag}
              className="flex items-center justify-between rounded border border-slate-100 bg-slate-50 px-3 py-2"
            >
              <span className="font-mono text-xs text-slate-700">{flag}</span>
              <span className={enabled ? "text-emerald-700" : "text-slate-500"}>
                {enabled ? "on" : "off"}
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
        <h2 className="text-lg font-semibold text-slate-900">{Copy.settings.plugins}</h2>
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
                      {String((plugin.health as { state?: string }).state ?? "unknown")}
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
