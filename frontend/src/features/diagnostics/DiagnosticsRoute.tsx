import { useCallback, useEffect, useState } from "react";
import { Copy } from "../../copy/copy";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useWorkerStore } from "../../stores/workerStore";
import type { Envelope, TasksListResponseTaskSummary } from "../../types/generated";

function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** exponent).toFixed(1)} ${units[exponent]}`;
}

export function DiagnosticsRoute() {
  const health = useWorkerStore((state) => state.health);
  const refreshHealth = useWorkerStore((state) => state.refresh);
  const [tasks, setTasks] = useState<TasksListResponseTaskSummary[]>([]);
  const [events, setEvents] = useState<Envelope[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTasks = useCallback(async () => {
    try {
      const result = await workerClient.listTasks({ limit: 20 });
      setTasks(result.tasks);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  useEffect(() => {
    void loadTasks();
    void refreshHealth();
    let unlisten: (() => void) | undefined;
    void workerClient.onEvent((event) => {
      setEvents((current) => [event, ...current].slice(0, 10));
      if (event.topic?.startsWith("task.")) {
        void loadTasks();
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
  }, [loadTasks, refreshHealth]);

  const startTask = async () => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.startSleepTask({ seconds: 0.15, steps: 3 });
      setTimeout(() => void loadTasks(), 250);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const cancelTask = async (taskId: string) => {
    try {
      await workerClient.cancelTask({ task_id: taskId });
      await loadTasks();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={Copy.diagnostics.title}
        description={Copy.diagnostics.description}
        actions={
          <>
            <button
              type="button"
              onClick={() => void startTask()}
              disabled={busy}
              className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {Copy.diagnostics.startTask}
            </button>
            <button
              type="button"
              onClick={() => void refreshHealth()}
              className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Refresh
            </button>
          </>
        }
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.diagnostics.health}</h2>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-slate-500">{Copy.diagnostics.status}</dt>
            <dd className="font-medium text-slate-900">{health?.status ?? Copy.settings.none}</dd>
          </div>
          <div>
            <dt className="text-slate-500">{Copy.settings.schemaVersion}</dt>
            <dd className="font-medium text-slate-900">
              {health?.schema_version ?? Copy.settings.none}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">{Copy.diagnostics.queueDepth}</dt>
            <dd className="font-medium text-slate-900">{health?.queue_depth ?? 0}</dd>
          </div>
          <div>
            <dt className="text-slate-500">{Copy.diagnostics.diskUsage}</dt>
            <dd className="font-medium text-slate-900">
              {formatBytes(health?.disk_usage_bytes ?? 0)}
            </dd>
          </div>
        </dl>
        <p className="mt-3 break-all font-mono text-xs text-slate-500">
          {health?.data_dir ?? Copy.settings.none}
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.diagnostics.recentTasks}</h2>
        {tasks.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">{Copy.diagnostics.noTasks}</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-2 py-2">{Copy.diagnostics.taskId}</th>
                  <th className="px-2 py-2">{Copy.diagnostics.taskType}</th>
                  <th className="px-2 py-2">{Copy.diagnostics.taskStatus}</th>
                  <th className="px-2 py-2">{Copy.diagnostics.taskProgress}</th>
                  <th className="px-2 py-2">{Copy.diagnostics.taskActions}</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id} className="border-b border-slate-100">
                    <td className="px-2 py-2 font-mono text-xs">{task.id}</td>
                    <td className="px-2 py-2">{task.type}</td>
                    <td className="px-2 py-2">{task.status}</td>
                    <td className="px-2 py-2 text-xs text-slate-600">
                      {JSON.stringify(task.progress)}
                    </td>
                    <td className="px-2 py-2">
                      {task.status === "queued" || task.status === "running" ? (
                        <button
                          type="button"
                          onClick={() => void cancelTask(task.id)}
                          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          {Copy.diagnostics.cancel}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.diagnostics.exportLogs}</h2>
        <p className="mt-2 text-sm text-slate-500">{Copy.diagnostics.exportLogsPlaceholder}</p>
        <button
          type="button"
          disabled
          title={Copy.diagnostics.exportLogsPlaceholder}
          className="mt-3 cursor-not-allowed rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-400"
        >
          {Copy.diagnostics.exportLogs}
        </button>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.diagnostics.events}</h2>
        {events.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">{Copy.diagnostics.noEvents}</p>
        ) : (
          <ul className="mt-2 space-y-1 font-mono text-xs text-slate-600">
            {events.map((event, index) => (
              <li key={`${event.topic}-${event.seq}-${index}`}>
                {event.topic} #{event.seq}
                {event.task_id ? ` task=${event.task_id}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
