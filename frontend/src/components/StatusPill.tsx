import { Copy } from "../copy/copy";
import type { ConnectionStatus } from "../stores/workerStore";

const styles: Record<ConnectionStatus, string> = {
  idle: "bg-slate-100 text-slate-700 border-slate-200",
  connecting: "bg-amber-50 text-amber-800 border-amber-200",
  ready: "bg-emerald-50 text-emerald-800 border-emerald-200",
  error: "bg-red-50 text-red-800 border-red-200",
};

export function StatusPill({ status }: { status: ConnectionStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${styles[status]}`}
      role="status"
      aria-live="polite"
    >
      {Copy.status[status]}
    </span>
  );
}
